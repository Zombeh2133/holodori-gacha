from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import struct
import sys
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from hashlib import md5
from pathlib import Path

import httpx
from Crypto.Cipher import AES
from dotenv import load_dotenv


PROJECT_ROOT = (
    Path(__file__)
    .resolve()
    .parents[1]
)

GAME_DATA_ROOT = (
    Path.home()
    / "AppData"
    / "LocalLow"
    / "QualiArts, Inc_"
    / "hololive-Dreams"
)

REGION_CONFIG = {
    "US": (
        "https://us.game-hololive-dreams.com"
    ),
    "ASIA": (
        "https://as.game-hololive-dreams.com"
    ),
    "JP": (
        "https://jp.game-hololive-dreams.com"
    ),
}

APPVER_URL = (
    "https://raw.githubusercontent.com/"
    "HolodoriDB/holodori-app-protos/"
    "refs/heads/main/{platform}/appver.json"
)

MASTER_RAW_BASE = (
    "https://raw.githubusercontent.com/"
    "HolodoriDB/holodori-db-eng-diff/"
    "main"
)

EMPTY_GRPC = (
    b"\x00"
    b"\x00\x00\x00\x00"
)

MASTER_HASH_RE = re.compile(
    r"^[0-9a-fA-F]{64}$"
)

CARD_RARITY_RE = re.compile(
    r"^card-[^-]+-(\d+)-",
    re.IGNORECASE,
)

GACHA_PICKUP_RE = re.compile(
    r"^gacha-pickup-"
    r"(normal|select)-"
    r"(\d{6})$",
    re.IGNORECASE,
)

USER_DATA_USER_FIELD = 342861366
USER_DATA_PROFILE_FIELD = 49977942

CACHE_MAX_AGE_SECONDS = 6 * 60 * 60


class LocalImportError(
    RuntimeError
):
    pass


def load_local_environment() -> None:
    candidates = [
        PROJECT_ROOT
        / ".env.local-importer",
        PROJECT_ROOT
        / ".env",
        PROJECT_ROOT.parent
        / "Leaderboard-bot"
        / ".env",
    ]

    for path in candidates:
        if path.exists():
            load_dotenv(
                path,
                override=False,
            )


def newest_dc_file() -> Path:
    if not GAME_DATA_ROOT.exists():
        raise LocalImportError(
            "hololive Dreams data folder was "
            "not found. Launch the game and "
            "log in first."
        )

    files = sorted(
        (
            path
            for path
            in GAME_DATA_ROOT.glob(
                "DC*"
            )
            if path.is_file()
        ),
        key=lambda path: (
            path.stat().st_mtime_ns
        ),
        reverse=True,
    )

    if not files:
        raise LocalImportError(
            "No hololive Dreams DC* account "
            "file was found. Launch the game "
            "and log in first."
        )

    return files[0]


def read_credential(
    path: Path,
) -> str:
    credential = (
        path.read_text(
            encoding="utf-8-sig"
        )
        .lstrip("\ufeff")
        .strip()
    )

    if not (
        32
        <= len(
            credential
        )
        <= 2048
    ):
        raise LocalImportError(
            "The newest DC* file did not "
            "contain a valid credential."
        )

    if any(
        ord(char) < 32
        or ord(char) > 126
        for char in credential
    ):
        raise LocalImportError(
            "The newest DC* file contained "
            "unexpected characters."
        )

    return credential


def device_ad_id() -> str:
    configured = str(
        os.getenv(
            "HD_APP_AD_ID",
            "",
        )
        or ""
    ).strip()

    if configured:
        return configured

    path = (
        PROJECT_ROOT
        / "data"
        / "local-importer-device.json"
    )

    if path.exists():
        try:
            payload = json.loads(
                path.read_text(
                    encoding="utf-8"
                )
            )
            value = str(
                payload.get(
                    "app_ad_id",
                    "",
                )
                or ""
            ).strip()

            if value:
                return value
        except Exception:
            pass

    value = str(
        uuid.uuid4()
    )

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    path.write_text(
        json.dumps(
            {
                "app_ad_id": value,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    return value


def x_as_for_region(
    region: str,
) -> str:
    region = str(
        region
    ).upper()

    return str(
        os.getenv(
            f"HD_X_AS_{region}",
            "",
        )
        or os.getenv(
            "HD_X_AS",
            "",
        )
        or ""
    ).strip()


def app_version_for_region(
    region: str,
) -> str:
    platform = (
        "jp"
        if str(
            region
        ).upper()
        == "JP"
        else "global"
    )

    try:
        response = httpx.get(
            APPVER_URL.format(
                platform=platform
            ),
            timeout=20,
            follow_redirects=True,
        )
        response.raise_for_status()
        payload = response.json()
        value = str(
            payload.get(
                "version_name",
                "",
            )
            or ""
        ).strip()
    except Exception as exc:
        raise LocalImportError(
            "Could not load the current "
            "Holodori app version for "
            f"{region}: {exc}"
        ) from exc

    if not value:
        raise LocalImportError(
            "The Holodori app-version source "
            f"returned no version for {region}."
        )

    return value


def encode_varint(
    value: int,
) -> bytes:
    value = int(
        value
    )

    if value < 0:
        value &= (
            (1 << 64)
            - 1
        )

    out = bytearray()

    while True:
        byte = value & 0x7F
        value >>= 7

        if value:
            out.append(
                byte
                | 0x80
            )
        else:
            out.append(
                byte
            )
            return bytes(
                out
            )


def read_varint(
    data: bytes,
    pos: int,
) -> tuple[
    int,
    int,
]:
    value = 0
    shift = 0

    while True:
        if pos >= len(
            data
        ):
            raise LocalImportError(
                "Truncated protobuf varint"
            )

        byte = data[pos]
        pos += 1

        value |= (
            (byte & 0x7F)
            << shift
        )

        if not (
            byte
            & 0x80
        ):
            return (
                value,
                pos,
            )

        shift += 7

        if shift > 70:
            raise LocalImportError(
                "Invalid protobuf varint"
            )


def parse_fields(
    data: bytes,
) -> list[
    tuple[
        int,
        int,
        object,
    ]
]:
    result = []
    pos = 0

    while pos < len(
        data
    ):
        key, pos = read_varint(
            data,
            pos,
        )

        number = key >> 3
        wire = key & 7

        if number == 0:
            raise LocalImportError(
                "Invalid protobuf field 0"
            )

        if wire == 0:
            value, pos = read_varint(
                data,
                pos,
            )

        elif wire == 1:
            if (
                pos + 8
                > len(
                    data
                )
            ):
                raise LocalImportError(
                    "Truncated fixed64"
                )

            value = data[
                pos:pos + 8
            ]
            pos += 8

        elif wire == 2:
            length, pos = read_varint(
                data,
                pos,
            )
            end = (
                pos
                + length
            )

            if end > len(
                data
            ):
                raise LocalImportError(
                    "Truncated protobuf field"
                )

            value = data[
                pos:end
            ]
            pos = end

        elif wire == 5:
            if (
                pos + 4
                > len(
                    data
                )
            ):
                raise LocalImportError(
                    "Truncated fixed32"
                )

            value = data[
                pos:pos + 4
            ]
            pos += 4

        else:
            raise LocalImportError(
                "Unsupported protobuf wire "
                f"type {wire}"
            )

        result.append(
            (
                number,
                wire,
                value,
            )
        )

    return result


def get_first(
    fields,
    number: int,
    wire: int | None = None,
):
    for (
        field_number,
        field_wire,
        value,
    ) in fields:
        if (
            field_number
            == number
            and (
                wire is None
                or wire
                == field_wire
            )
        ):
            return value

    return None


def get_all(
    fields,
    number: int,
    wire: int | None = None,
):
    return [
        value
        for (
            field_number,
            field_wire,
            value,
        )
        in fields
        if (
            field_number
            == number
            and (
                wire is None
                or wire
                == field_wire
            )
        )
    ]


def decode_text(
    value,
) -> str:
    if value is None:
        return ""

    try:
        return bytes(
            value
        ).decode(
            "utf-8"
        )
    except UnicodeDecodeError:
        return ""


def protobuf_string(
    field_number: int,
    value: str,
) -> bytes:
    raw = str(
        value
    ).encode(
        "utf-8"
    )

    return (
        encode_varint(
            (
                int(
                    field_number
                )
                << 3
            )
            | 2
        )
        + encode_varint(
            len(
                raw
            )
        )
        + raw
    )


def dotnet_ticks_now() -> int:
    return (
        621355968000000000
        + (
            time.time_ns()
            // 100
        )
    )


def build_encrypted_request(
    plaintext: bytes,
    secret: str,
) -> tuple[
    bytes,
    int,
]:
    ticks = dotnet_ticks_now()
    msg_key = struct.pack(
        "<Q",
        ticks,
    )

    secret_bytes = secret.encode(
        "utf-8"
    )
    aes_key = md5(
        secret_bytes
    ).digest()
    iv = md5(
        secret_bytes
        + msg_key
    ).digest()

    pad = (
        16
        - (
            len(
                plaintext
            )
            % 16
        )
    )

    padded = (
        plaintext
        + bytes(
            [pad]
        )
        * pad
    )

    ciphertext = AES.new(
        aes_key,
        AES.MODE_CBC,
        iv,
    ).encrypt(
        padded
    )

    proto_enc = (
        struct.pack(
            "<H",
            len(
                msg_key
            )
            + 2,
        )
        + b"\x00"
        + bytes(
            [
                len(
                    msg_key
                )
            ]
        )
        + msg_key
        + ciphertext
    )

    grpc = (
        b"\x00"
        + len(
            proto_enc
        ).to_bytes(
            4,
            "big",
        )
        + proto_enc
    )

    return (
        grpc,
        ticks,
    )


def decrypt_response(
    src: bytes,
    secret: str,
) -> bytes:
    if len(
        src
    ) < 5:
        raise LocalImportError(
            "Response too short"
        )

    if src[0] != 0:
        raise LocalImportError(
            "Unexpected gRPC compression flag"
        )

    grpc_len = int.from_bytes(
        src[1:5],
        "big",
    )
    payload = src[
        5:5
        + grpc_len
    ]

    if len(
        payload
    ) != grpc_len:
        raise LocalImportError(
            "Truncated gRPC response"
        )

    if len(
        payload
    ) < 4:
        raise LocalImportError(
            "proto-enc payload too short"
        )

    header_len = int.from_bytes(
        payload[0:2],
        "little",
    )
    compressed = bool(
        payload[2]
        & 1
    )
    key_len = payload[3]

    if (
        header_len
        != key_len
        + 2
    ):
        raise LocalImportError(
            "Unexpected proto-enc header"
        )

    msg_key = payload[
        4:4
        + key_len
    ]
    ciphertext = payload[
        4
        + key_len:
    ]

    if (
        len(
            ciphertext
        )
        % 16
    ):
        raise LocalImportError(
            "Encrypted response was not "
            "AES aligned"
        )

    secret_bytes = secret.encode(
        "utf-8"
    )
    aes_key = md5(
        secret_bytes
    ).digest()
    iv = md5(
        secret_bytes
        + msg_key
    ).digest()

    plain = AES.new(
        aes_key,
        AES.MODE_CBC,
        iv,
    ).decrypt(
        ciphertext
    )

    if not plain:
        raise LocalImportError(
            "Empty decrypted response"
        )

    pad = plain[-1]

    if not (
        1
        <= pad
        <= 16
    ):
        raise LocalImportError(
            "Invalid decrypted padding"
        )

    if (
        plain[
            -pad:
        ]
        != bytes(
            [pad]
        )
        * pad
    ):
        raise LocalImportError(
            "Invalid decrypted padding"
        )

    plain = plain[
        :-pad
    ]

    if compressed:
        import gzip

        plain = gzip.decompress(
            plain
        )

    return plain


def decode_response_flexible(
    src: bytes,
    secret: str,
) -> bytes:
    try:
        return decrypt_response(
            src,
            secret,
        )
    except Exception:
        pass

    if len(
        src
    ) < 5:
        raise LocalImportError(
            "Response too short"
        )

    if src[0] != 0:
        raise LocalImportError(
            "Unexpected gRPC compression flag"
        )

    grpc_len = int.from_bytes(
        src[1:5],
        "big",
    )
    payload = src[
        5:5
        + grpc_len
    ]

    if len(
        payload
    ) != grpc_len:
        raise LocalImportError(
            "Truncated gRPC response"
        )

    return payload


def extract_master_version(
    plain: bytes,
) -> str | None:
    raw = get_first(
        parse_fields(
            plain
        ),
        1,
        2,
    )

    if not raw:
        return None

    try:
        value = bytes(
            raw
        ).decode(
            "ascii"
        ).strip()
    except UnicodeDecodeError:
        return None

    if not MASTER_HASH_RE.fullmatch(
        value
    ):
        return None

    return value


def decode_gacha_history(
    plain: bytes,
) -> list[
    dict
]:
    rows = []

    for raw_history in get_all(
        parse_fields(
            plain
        ),
        1,
        2,
    ):
        fields = parse_fields(
            raw_history
        )

        rows.append(
            {
                "gacha_name": (
                    decode_text(
                        get_first(
                            fields,
                            1,
                            2,
                        )
                    )
                ),
                "card_id": (
                    decode_text(
                        get_first(
                            fields,
                            2,
                            2,
                        )
                    )
                ),
                "draw_time": int(
                    get_first(
                        fields,
                        3,
                        0,
                    )
                    or 0
                ),
            }
        )

    return rows


def decode_gacha_list(
    plain: bytes,
) -> list[
    dict
]:
    rows = []

    for raw_group in get_all(
        parse_fields(
            plain
        ),
        1,
        2,
    ):
        group = parse_fields(
            raw_group
        )

        for raw_gacha in get_all(
            group,
            3,
            2,
        ):
            fields = parse_fields(
                raw_gacha
            )

            rows.append(
                {
                    "gacha_id": (
                        decode_text(
                            get_first(
                                fields,
                                1,
                                2,
                            )
                        )
                    ),
                    "name": (
                        decode_text(
                            get_first(
                                fields,
                                3,
                                2,
                            )
                        )
                    ),
                    "start_time": int(
                        get_first(
                            fields,
                            6,
                            0,
                        )
                        or 0
                    ),
                    "end_time": int(
                        get_first(
                            fields,
                            7,
                            0,
                        )
                        or 0
                    ),
                    "promotion_image_asset_id": (
                        decode_text(
                            get_first(
                                fields,
                                14,
                                2,
                            )
                        )
                    ),
                }
            )

    return rows


def decode_user_identity(
    plain: bytes,
) -> tuple[
    str | None,
    str | None,
]:
    outer = parse_fields(
        plain
    )
    user_data_raw = get_first(
        outer,
        1,
        2,
    )

    if not user_data_raw:
        return (
            None,
            None,
        )

    user_data = parse_fields(
        user_data_raw
    )

    public_user_id = None
    profile_name = None

    user_raw = get_first(
        user_data,
        USER_DATA_USER_FIELD,
        2,
    )

    if user_raw:
        user_fields = parse_fields(
            user_raw
        )
        public_user_id = (
            decode_text(
                get_first(
                    user_fields,
                    2,
                    2,
                )
            )
            or None
        )

    profile_raw = get_first(
        user_data,
        USER_DATA_PROFILE_FIELD,
        2,
    )

    if profile_raw:
        profile_fields = parse_fields(
            profile_raw
        )
        profile_name = (
            decode_text(
                get_first(
                    profile_fields,
                    2,
                    2,
                )
            )
            or None
        )

    return (
        public_user_id,
        profile_name,
    )


def infer_banner_type(
    gacha_id: str | None,
) -> str:
    value = str(
        gacha_id
        or ""
    ).casefold()

    if "select" in value:
        return "select"
    if "pickup" in value:
        return "pickup"
    if "beginner" in value:
        return "beginner"
    if "common" in value:
        return "standard"

    return "gacha"


def infer_card_rarity(
    card_id: str,
) -> int | None:
    match = CARD_RARITY_RE.match(
        str(
            card_id
            or ""
        )
    )

    if not match:
        return None

    try:
        return int(
            match.group(1)
        )
    except ValueError:
        return None


class GameClient:
    def __init__(
        self,
        *,
        region: str,
        credential: str,
        secret: str,
        x_as: str,
        app_ad_id: str,
        app_version: str,
    ) -> None:
        self.region = str(
            region
        ).upper()
        self.base_url = (
            REGION_CONFIG[
                self.region
            ]
        )
        self.credential = (
            credential
        )
        self.secret = secret
        self.x_as = x_as
        self.app_ad_id = (
            app_ad_id
        )
        self.app_version = (
            app_version
        )

        self.master_version = None
        self.auth_token = None

        self.client = httpx.Client(
            http2=True,
            timeout=30,
            limits=httpx.Limits(
                max_connections=8,
                max_keepalive_connections=4,
                keepalive_expiry=30,
            ),
            follow_redirects=True,
        )

    def close(self) -> None:
        self.client.close()

    def headers(
        self,
        *,
        ticks: int | None = None,
        authenticated: bool = False,
    ) -> dict:
        if ticks is None:
            ticks = dotnet_ticks_now()

        headers = {
            "content-type": (
                "application/grpc+proto-enc"
            ),
            "te": "trailers",
            "grpc-accept-encoding": (
                "identity,gzip"
            ),
            "user-agent": (
                "grpc-dotnet/2.76.0 "
                "(Mono Unity; "
                "CLR 4.0.30319.17020; "
                "netstandard2.1; arm64)"
            ),
            "x-app-version": (
                self.app_version
            ),
            "x-app-request-id": str(
                ticks
            ),
            "x-app-device-name": (
                "ROG ASUS AI2201_B"
            ),
            "x-app-os-version": (
                "Android OS 14 / "
                "API-34 "
                "(UQ1A.240205.07131809/"
                "ucsm.20260713.180933)"
            ),
            "x-app-lang-type": "eng",
            "x-app-os-type": "Android",
            "x-app-store-type": (
                "GooglePlay"
            ),
            "x-app-bundle-id": (
                "game.qualiarts."
                "hololive.dreams.com"
            ),
            "x-app-cpu": (
                "x86-64 SSE3 "
                "SSE4.1 SSE4.2 AVX"
            ),
            "x-app-gpu": (
                "Adreno (TM) 750"
            ),
            "x-app-operating-system-family": (
                "Other"
            ),
            "x-app-device-type": (
                "Handheld"
            ),
            "x-app-graphics-device-type": (
                "Vulkan"
            ),
            "x-app-graphics-device-version": (
                "Vulkan 1.1.0 "
                "[592.4.0 (0x94004000)]"
            ),
            "x-app-graphics-device-vendor-id": (
                "20803"
            ),
            "x-app-graphics-device-memory-size": (
                "1536"
            ),
            "x-app-graphics-shader-level": (
                "50"
            ),
            "x-app-processor-model": (
                "x86-64 SSE3 "
                "SSE4.1 SSE4.2 AVX"
            ),
            "x-app-processor-count": "6",
            "x-app-system-memory-size": (
                "5944"
            ),
            "x-app-screen-dpi": "480",
            "x-app-screen-height": (
                "878"
            ),
            "x-app-screen-width": (
                "1560"
            ),
            "x-a-ise": "False",
            "x-a-isda": "False",
            "x-a-isop": "False",
            "x-a-isr": "True",
            "x-a-s": self.x_as,
            "x-app-ad-id": (
                self.app_ad_id
            ),
            "grpc-timeout": "10S",
        }

        if self.master_version:
            headers[
                "x-app-master-version"
            ] = self.master_version

        if authenticated:
            if not self.auth_token:
                raise LocalImportError(
                    "Authenticated request "
                    "without a game token"
                )

            headers[
                "x-app-auth-token"
            ] = self.auth_token

        return headers

    def empty_rpc(
        self,
        path: str,
        *,
        authenticated: bool = False,
        allow_empty: bool = False,
    ) -> bytes:
        response = self.client.post(
            self.base_url
            + path,
            headers=self.headers(
                authenticated=authenticated
            ),
            content=EMPTY_GRPC,
        )

        if (
            response.status_code
            != 200
        ):
            raise LocalImportError(
                f"{path} failed: HTTP "
                f"{response.status_code}"
            )

        if (
            not response.content
            and not allow_empty
        ):
            raise LocalImportError(
                f"{path} returned no data"
            )

        return bytes(
            response.content
        )

    def refresh_master_version(
        self,
    ) -> str:
        attempts = [
            None,
            "0" * 64,
        ]

        for candidate in attempts:
            request_headers = (
                self.headers(
                    authenticated=False
                )
            )

            if candidate is None:
                request_headers.pop(
                    "x-app-master-version",
                    None,
                )
            else:
                request_headers[
                    "x-app-master-version"
                ] = candidate

            response = self.client.post(
                self.base_url
                + "/rpc.api.Master/Get",
                headers=request_headers,
                content=EMPTY_GRPC,
            )

            if (
                response.status_code
                != 200
                or not response.content
            ):
                continue

            try:
                plain = (
                    decode_response_flexible(
                        bytes(
                            response.content
                        ),
                        self.secret,
                    )
                )
                value = (
                    extract_master_version(
                        plain
                    )
                )
            except Exception:
                value = None

            if value:
                self.master_version = (
                    value
                )
                return value

        raise LocalImportError(
            "Could not discover the current "
            "Holodori Master version."
        )

    def login(self) -> None:
        self.refresh_master_version()

        plain = protobuf_string(
            1,
            self.credential,
        )

        request, ticks = (
            build_encrypted_request(
                plain,
                self.secret,
            )
        )

        response = self.client.post(
            self.base_url
            + "/rpc.api.Auth/Login",
            headers=self.headers(
                ticks=ticks,
                authenticated=False,
            ),
            content=request,
        )

        if (
            response.status_code
            != 200
            or not response.content
        ):
            raise LocalImportError(
                "Auth/Login failed: HTTP "
                f"{response.status_code}, "
                f"{len(response.content)} bytes"
            )

        plain_response = (
            decode_response_flexible(
                bytes(
                    response.content
                ),
                self.secret,
            )
        )

        token = decode_text(
            get_first(
                parse_fields(
                    plain_response
                ),
                1,
                2,
            )
        )

        if not token:
            raise LocalImportError(
                "Auth/Login returned no "
                "game auth token"
            )

        self.auth_token = token

        # Reproduce the lightweight state-setting requests that are safe
        # and useful to the importer. Failures are non-fatal because
        # Gacha/ListHistory is the actual success criterion.
        for path in (
            "/rpc.api.Home/Login",
            "/rpc.api.LoginBonus/Check",
        ):
            try:
                self.empty_rpc(
                    path,
                    authenticated=True,
                    allow_empty=True,
                )
            except Exception:
                pass

    def user_identity(
        self,
    ) -> tuple[
        str | None,
        str | None,
    ]:
        try:
            content = self.empty_rpc(
                "/rpc.api.User/Get",
                authenticated=True,
            )
            plain = (
                decode_response_flexible(
                    content,
                    self.secret,
                )
            )
            return decode_user_identity(
                plain
            )
        except Exception:
            return (
                None,
                None,
            )

    def gacha_list(
        self,
    ) -> list[
        dict
    ]:
        try:
            content = self.empty_rpc(
                "/rpc.api.Gacha/List",
                authenticated=True,
            )
            plain = (
                decode_response_flexible(
                    content,
                    self.secret,
                )
            )
            return decode_gacha_list(
                plain
            )
        except Exception:
            return []

    def gacha_history(
        self,
    ) -> list[
        dict
    ]:
        content = self.empty_rpc(
            "/rpc.api.Gacha/ListHistory",
            authenticated=True,
        )
        plain = (
            decode_response_flexible(
                content,
                self.secret,
            )
        )
        return decode_gacha_history(
            plain
        )


def load_json_file(
    path: Path,
):
    return json.loads(
        path.read_text(
            encoding="utf-8"
        )
    )


def fetch_cached_json(
    filename: str,
):
    cache_dir = (
        PROJECT_ROOT
        / "data"
        / "local-importer-cache"
        / "master"
    )
    cache_dir.mkdir(
        parents=True,
        exist_ok=True,
    )
    cache_path = (
        cache_dir
        / filename
    )

    sibling = (
        PROJECT_ROOT.parent
        / "holodori-db-eng-diff"
        / filename
    )

    if sibling.exists():
        return load_json_file(
            sibling
        )

    if cache_path.exists():
        age = (
            time.time()
            - cache_path.stat().st_mtime
        )

        if (
            age
            < CACHE_MAX_AGE_SECONDS
        ):
            return load_json_file(
                cache_path
            )

    try:
        response = httpx.get(
            MASTER_RAW_BASE
            + "/"
            + filename,
            timeout=30,
            follow_redirects=True,
        )
        response.raise_for_status()
        payload = response.json()

        cache_path.write_text(
            json.dumps(
                payload,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        return payload

    except Exception:
        if cache_path.exists():
            return load_json_file(
                cache_path
            )

        raise


def card_metadata() -> dict[
    str,
    dict,
]:
    try:
        card_rows = (
            fetch_cached_json(
                "Card.json"
            )
        )
        character_rows = (
            fetch_cached_json(
                "Character.json"
            )
        )
        lang_rows = (
            fetch_cached_json(
                "LangCard_Eng.json"
            )
        )
    except Exception as exc:
        print(
            "Warning: card metadata could "
            f"not be refreshed: {exc}"
        )
        return {}

    lang = {}

    for row in (
        lang_rows
        or []
    ):
        data = (
            row.get(
                "data"
            )
            if isinstance(
                row,
                dict,
            )
            else {}
        )

        if not isinstance(
            data,
            dict,
        ):
            data = {}

        key = str(
            row.get(
                "id"
            )
            or data.get(
                "id"
            )
            or ""
        ).strip()
        text = str(
            data.get(
                "text"
            )
            or ""
        ).strip()

        if key and text:
            lang[key] = text

    characters = {}

    for row in (
        character_rows
        or []
    ):
        data = (
            row.get(
                "data"
            )
            if isinstance(
                row,
                dict,
            )
            else {}
        )

        if not isinstance(
            data,
            dict,
        ):
            data = {}

        key = str(
            row.get(
                "id"
            )
            or data.get(
                "id"
            )
            or ""
        ).strip()

        if not key:
            continue

        characters[
            key
        ] = str(
            data.get(
                "nameEng"
            )
            or data.get(
                "shortNameEng"
            )
            or key
        ).strip()

    result = {}

    for row in (
        card_rows
        or []
    ):
        data = (
            row.get(
                "data"
            )
            if isinstance(
                row,
                dict,
            )
            else {}
        )

        if not isinstance(
            data,
            dict,
        ):
            data = {}

        card_id = str(
            row.get(
                "id"
            )
            or data.get(
                "id"
            )
            or ""
        ).strip()

        if not card_id:
            continue

        rarity_value = str(
            data.get(
                "rarity"
            )
            or ""
        ).strip()

        rarity = None

        if rarity_value:
            try:
                rarity = int(
                    rarity_value.rsplit(
                        "_",
                        1,
                    )[-1]
                )
            except Exception:
                rarity = None

        character_id = str(
            data.get(
                "characterId"
            )
            or ""
        ).strip()

        result[
            card_id
        ] = {
            "name": (
                lang.get(
                    str(
                        data.get(
                            "nameLangId"
                        )
                        or ""
                    )
                )
                or card_id
            ),
            "rarity": (
                rarity
                or infer_card_rarity(
                    card_id
                )
            ),
            "character_id": (
                character_id
            ),
            "character_name": (
                characters.get(
                    character_id
                )
                or character_id
            ),
        }

    return result


def local_account_fallback(
    credential: str,
) -> str:
    mapping_path = (
        PROJECT_ROOT
        / "data"
        / "local-importer-accounts.json"
    )
    mapping_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fingerprint = (
        hashlib.sha256(
            credential.encode(
                "ascii"
            )
        ).hexdigest()
    )

    payload = {}

    if mapping_path.exists():
        try:
            payload = json.loads(
                mapping_path.read_text(
                    encoding="utf-8"
                )
            )
        except Exception:
            payload = {}

    value = str(
        payload.get(
            fingerprint,
            "",
        )
        or ""
    ).strip()

    if not value:
        value = (
            "local-"
            + uuid.uuid4().hex[
                :20
            ]
        )
        payload[
            fingerprint
        ] = value

        mapping_path.write_text(
            json.dumps(
                payload,
                indent=2,
            ),
            encoding="utf-8",
        )

    return value


def fetch_game_history(
    credential: str,
    preferred_region: str | None,
):
    secret = str(
        os.getenv(
            "HD_MARSHALLER_SECRET",
            "",
        )
        or ""
    ).strip()

    if not secret:
        raise LocalImportError(
            "HD_MARSHALLER_SECRET is missing. "
            "Add the protocol settings already "
            "used by Gii Murin to "
            ".env.local-importer."
        )

    region_order = [
        "US",
        "ASIA",
        "JP",
    ]

    if preferred_region:
        region = (
            preferred_region.upper()
        )

        if region not in (
            "US",
            "ASIA",
            "JP",
        ):
            raise LocalImportError(
                "--region must be US, ASIA, or JP"
            )

        region_order = [
            region
        ]

    app_ad_id = device_ad_id()
    failures = []

    for region in region_order:
        x_as = x_as_for_region(
            region
        )

        if not x_as:
            failures.append(
                (
                    region,
                    "missing x-a-s setting",
                )
            )
            continue

        client = None

        try:
            print(
                f"Trying {region}..."
            )

            client = GameClient(
                region=region,
                credential=(
                    credential
                ),
                secret=secret,
                x_as=x_as,
                app_ad_id=(
                    app_ad_id
                ),
                app_version=(
                    app_version_for_region(
                        region
                    )
                ),
            )

            client.login()

            (
                public_user_id,
                profile_name,
            ) = client.user_identity()

            gacha_list = (
                client.gacha_list()
            )

            history = (
                client.gacha_history()
            )

            history = [
                row
                for row in history
                if (
                    str(
                        row.get(
                            "card_id"
                        )
                        or ""
                    ).strip()
                    and int(
                        row.get(
                            "draw_time"
                        )
                        or 0
                    )
                    > 0
                )
            ]

            return {
                "region": region,
                "public_user_id": (
                    public_user_id
                ),
                "profile_name": (
                    profile_name
                ),
                "gacha_list": (
                    gacha_list
                ),
                "history": (
                    history
                ),
            }

        except Exception as exc:
            failures.append(
                (
                    region,
                    (
                        f"{type(exc).__name__}: "
                        f"{exc}"
                    ),
                )
            )

        finally:
            if client is not None:
                client.close()

    details = "; ".join(
        (
            f"{region}: {error}"
        )
        for region, error
        in failures
    )

    raise LocalImportError(
        "Could not authenticate the newest "
        "Holodori account in any configured "
        "region. "
        + details
    )


def normalize_export(
    *,
    credential: str,
    game_result: dict,
) -> dict:
    region = str(
        game_result[
            "region"
        ]
    ).upper()

    history = list(
        game_result.get(
            "history"
        )
        or []
    )

    metadata = (
        card_metadata()
    )

    live_by_name = {}

    for item in (
        game_result.get(
            "gacha_list"
        )
        or []
    ):
        name = str(
            item.get(
                "name"
            )
            or ""
        ).strip()

        if not name:
            continue

        live_by_name.setdefault(
            name.casefold(),
            item,
        )

    occurrence_counts = (
        defaultdict(
            int
        )
    )
    pulls = []

    # Oldest first makes occurrence indexes and later server pull_order
    # deterministic across repeat imports.
    history.sort(
        key=lambda row: (
            int(
                row.get(
                    "draw_time"
                )
                or 0
            ),
            str(
                row.get(
                    "gacha_name"
                )
                or ""
            ),
            str(
                row.get(
                    "card_id"
                )
                or ""
            ),
        )
    )

    for row in history:
        gacha_name = str(
            row.get(
                "gacha_name"
            )
            or "Unknown Gacha"
        ).strip()

        card_id = str(
            row.get(
                "card_id"
            )
            or ""
        ).strip()

        draw_time = int(
            row.get(
                "draw_time"
            )
            or 0
        )

        match = live_by_name.get(
            gacha_name.casefold()
        )

        game_banner_id = (
            str(
                match.get(
                    "gacha_id"
                )
                or ""
            ).strip()
            if match
            else ""
        )

        card = metadata.get(
            card_id,
            {},
        )

        rarity = (
            card.get(
                "rarity"
            )
            or infer_card_rarity(
                card_id
            )
        )

        if not rarity:
            print(
                "Warning: could not determine "
                f"rarity for {card_id}; skipping."
            )
            continue

        occurrence_key = (
            gacha_name,
            card_id,
            draw_time,
        )
        occurrence_counts[
            occurrence_key
        ] += 1

        pulls.append(
            {
                "gacha_name": (
                    gacha_name
                ),
                "game_banner_id": (
                    game_banner_id
                    or None
                ),
                "banner_type": (
                    infer_banner_type(
                        game_banner_id
                    )
                ),
                "card_id": card_id,
                "item_name": (
                    card.get(
                        "name"
                    )
                    or card_id
                ),
                "rarity": int(
                    rarity
                ),
                "draw_time": (
                    draw_time
                ),
                "occurrence_index": (
                    occurrence_counts[
                        occurrence_key
                    ]
                ),
            }
        )

    public_user_id = str(
        game_result.get(
            "public_user_id"
        )
        or ""
    ).strip()

    if not public_user_id:
        public_user_id = (
            local_account_fallback(
                credential
            )
        )
        print(
            "Warning: User/Get did not expose "
            "a public user ID; using a stable "
            "local account ID for this PC."
        )

    profile_name = str(
        game_result.get(
            "profile_name"
        )
        or ""
    ).strip()

    return {
        "region": region,
        "game_account_id": (
            public_user_id
        ),
        "account_nickname": (
            profile_name
            or f"{region} Account"
        ),
        "importer_version": (
            "local-game-v1"
        ),
        "pulls": pulls,
    }


def save_preview(
    payload: dict,
) -> Path:
    output_dir = (
        PROJECT_ROOT
        / "data"
        / "import-preview"
    )
    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    timestamp = datetime.now(
        timezone.utc
    ).strftime(
        "%Y%m%d-%H%M%S"
    )

    path = (
        output_dir
        / (
            "holodori-"
            + str(
                payload.get(
                    "region"
                )
                or "unknown"
            ).lower()
            + "-"
            + timestamp
            + ".json"
        )
    )

    path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    return path


def upload_payload(
    *,
    api_base: str,
    token: str,
    payload: dict,
) -> dict:
    url = (
        str(
            api_base
        )
        .rstrip(
            "/"
        )
        + "/api/import/local"
    )

    response = httpx.post(
        url,
        headers={
            "Authorization": (
                "Bearer "
                + token
            ),
            "Content-Type": (
                "application/json"
            ),
        },
        json=payload,
        timeout=60,
        follow_redirects=True,
    )

    try:
        result = response.json()
    except Exception:
        result = None

    if (
        response.status_code
        != 200
    ):
        detail = (
            result.get(
                "detail"
            )
            if isinstance(
                result,
                dict,
            )
            else None
        )

        raise LocalImportError(
            "Site upload failed: HTTP "
            f"{response.status_code}"
            + (
                f" - {detail}"
                if detail
                else ""
            )
        )

    if not isinstance(
        result,
        dict,
    ):
        raise LocalImportError(
            "Site upload returned an "
            "unexpected response."
        )

    return result


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Import Holodori Gacha history "
            "without sending the game "
            "credential to the website."
        )
    )

    parser.add_argument(
        "--token",
        default="",
        help=(
            "Short-lived local import token "
            "generated by the website."
        ),
    )
    parser.add_argument(
        "--api-base",
        default=(
            "http://127.0.0.1:8000"
        ),
    )
    parser.add_argument(
        "--region",
        default="",
        help=(
            "Optional region override: "
            "US, ASIA, or JP."
        ),
    )
    parser.add_argument(
        "--export-only",
        action="store_true",
        help=(
            "Fetch and save JSON without "
            "uploading it."
        ),
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_local_environment()

    print()
    print(
        "Holodori Gacha Local Importer"
    )
    print(
        "============================="
    )
    print()

    try:
        dc_file = newest_dc_file()
        credential = read_credential(
            dc_file
        )

        print(
            "Account file:",
            dc_file.name,
        )
        print(
            "Credential stays on this PC."
        )
        print(
            "Fetching Gacha/ListHistory "
            "from the official game API..."
        )
        print()

        result = fetch_game_history(
            credential,
            (
                args.region
                or None
            ),
        )

        payload = normalize_export(
            credential=credential,
            game_result=result,
        )

        preview = save_preview(
            payload
        )

        print()
        print(
            "Region:",
            payload[
                "region"
            ],
        )
        print(
            "Game account:",
            payload[
                "account_nickname"
            ],
        )
        print(
            "Public ID:",
            payload[
                "game_account_id"
            ],
        )
        print(
            "Pulls found:",
            len(
                payload[
                    "pulls"
                ]
            ),
        )
        print(
            "Preview saved:",
            preview,
        )

        if args.export_only:
            print()
            print(
                "Export-only mode complete."
            )
            return 0

        token = str(
            args.token
            or ""
        ).strip()

        if not token:
            raise LocalImportError(
                "No local import token was "
                "provided. Generate a command "
                "from the website Import tab, "
                "or use --export-only."
            )

        print()
        print(
            "Uploading normalized pull "
            "history to Holodori Gacha..."
        )

        upload = upload_payload(
            api_base=args.api_base,
            token=token,
            payload=payload,
        )

        print()
        print(
            "Import complete."
        )
        print(
            "Submitted:",
            upload.get(
                "total_pulls",
                0,
            ),
        )
        print(
            "New:",
            upload.get(
                "new_pulls",
                0,
            ),
        )
        print(
            "Duplicates:",
            upload.get(
                "duplicate_pulls",
                0,
            ),
        )
        print()

        return 0

    except LocalImportError as exc:
        print()
        print(
            "Import failed:"
        )
        print(
            str(
                exc
            )
        )
        print()

        return 1

    finally:
        # Drop the only in-process reference as soon as the run ends.
        try:
            credential = None
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
