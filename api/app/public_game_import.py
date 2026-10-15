from __future__ import annotations

import hashlib
import os
import re
import struct
import time
from collections import defaultdict
from hashlib import md5

import httpx
from Crypto.Cipher import AES


REGION_CONFIG = {
    "US": {
        "base_url": "https://us.game-hololive-dreams.com",
        "platform": "global",
        "bundle_id": "game.qualiarts.hololive.dreams.com",
        "lang_type": "eng",
    },
    "ASIA": {
        "base_url": "https://as.game-hololive-dreams.com",
        "platform": "global",
        "bundle_id": "game.qualiarts.hololive.dreams.com",
        "lang_type": "eng",
    },
    "JP": {
        "base_url": "https://jp.game-hololive-dreams.com",
        "platform": "jp",
        "bundle_id": "game.qualiarts.hololive.dreams.jp",
        "lang_type": "eng",
    },
}

REGION_ORDER = ("US", "ASIA", "JP")

APPVER_URL = (
    "https://raw.githubusercontent.com/"
    "HolodoriDB/holodori-app-protos/"
    "refs/heads/main/{platform}/appver.json"
)

MASTER_RAW_BASE = (
    "https://raw.githubusercontent.com/"
    "HolodoriDB/holodori-db-eng-diff/main"
)

EMPTY_GRPC = b"\x00\x00\x00\x00\x00"

MASTER_HASH_RE = re.compile(
    r"^[0-9a-fA-F]{64}$"
)
CARD_RARITY_RE = re.compile(
    r"^card-[^-]+-(\d+)-",
    re.IGNORECASE,
)

USER_DATA_USER_FIELD = 342861366
USER_DATA_PROFILE_FIELD = 49977942

_METADATA_CACHE: dict[str, dict] | None = None
_METADATA_CACHE_AT = 0.0
_METADATA_TTL_SECONDS = 6 * 60 * 60

_APP_VERSION_CACHE: dict[str, tuple[float, str]] = {}
_APP_VERSION_TTL_SECONDS = 10 * 60


class PublicGameImportError(RuntimeError):
    pass


def validate_credential(credential: str) -> str:
    value = str(
        credential or ""
    ).lstrip("\ufeff").strip()

    if not (32 <= len(value) <= 2048):
        raise PublicGameImportError(
            "The hololive Dreams account credential was not valid."
        )

    if any(
        ord(char) < 32
        or ord(char) > 126
        for char in value
    ):
        raise PublicGameImportError(
            "The hololive Dreams account credential contained unexpected characters."
        )

    return value


def x_as_value() -> str:
    return str(
        os.getenv(
            "HD_X_AS",
            "",
        )
        or ""
    ).strip()


def app_version_for_region(region: str) -> str:
    region = str(region).upper()
    config = REGION_CONFIG.get(region)

    if not config:
        raise PublicGameImportError(
            "Unsupported hololive Dreams region."
        )

    platform = str(config["platform"])
    now = time.monotonic()
    cached = _APP_VERSION_CACHE.get(platform)

    if (
        cached
        and (now - cached[0]) < _APP_VERSION_TTL_SECONDS
    ):
        return cached[1]

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
        raise PublicGameImportError(
            "Could not load the current hololive Dreams app version."
        ) from exc

    if not value:
        raise PublicGameImportError(
            "The hololive Dreams app-version source returned no version."
        )

    _APP_VERSION_CACHE[platform] = (now, value)
    return value


def encode_varint(value: int) -> bytes:
    value = int(value)

    if value < 0:
        value &= (1 << 64) - 1

    out = bytearray()

    while True:
        byte = value & 0x7F
        value >>= 7

        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def read_varint(
    data: bytes,
    pos: int,
) -> tuple[int, int]:
    value = 0
    shift = 0

    while True:
        if pos >= len(data):
            raise PublicGameImportError(
                "Truncated protobuf varint"
            )

        byte = data[pos]
        pos += 1
        value |= (
            (byte & 0x7F)
            << shift
        )

        if not (
            byte & 0x80
        ):
            return value, pos

        shift += 7

        if shift > 70:
            raise PublicGameImportError(
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

    while pos < len(data):
        key, pos = read_varint(
            data,
            pos,
        )
        number = key >> 3
        wire = key & 7

        if number == 0:
            raise PublicGameImportError(
                "Invalid protobuf field"
            )

        if wire == 0:
            value, pos = read_varint(
                data,
                pos,
            )
        elif wire == 1:
            if pos + 8 > len(data):
                raise PublicGameImportError(
                    "Truncated fixed64"
                )
            value = data[pos:pos + 8]
            pos += 8
        elif wire == 2:
            length, pos = read_varint(
                data,
                pos,
            )
            end = pos + length
            if end > len(data):
                raise PublicGameImportError(
                    "Truncated protobuf field"
                )
            value = data[pos:end]
            pos = end
        elif wire == 5:
            if pos + 4 > len(data):
                raise PublicGameImportError(
                    "Truncated fixed32"
                )
            value = data[pos:pos + 4]
            pos += 4
        else:
            raise PublicGameImportError(
                "Unsupported protobuf wire type"
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
            field_number == number
            and (
                wire is None
                or wire == field_wire
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
        ) in fields
        if (
            field_number == number
            and (
                wire is None
                or wire == field_wire
            )
        )
    ]


def decode_text(value) -> str:
    if value is None:
        return ""

    try:
        return bytes(value).decode(
            "utf-8"
        )
    except UnicodeDecodeError:
        return ""


def protobuf_string(
    field_number: int,
    value: str,
) -> bytes:
    raw = str(value).encode(
        "utf-8"
    )

    return (
        encode_varint(
            (int(field_number) << 3)
            | 2
        )
        + encode_varint(
            len(raw)
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
) -> tuple[bytes, int]:
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

    pad = 16 - (
        len(plaintext)
        % 16
    )
    padded = (
        plaintext
        + bytes([pad]) * pad
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
            len(msg_key) + 2,
        )
        + b"\x00"
        + bytes([len(msg_key)])
        + msg_key
        + ciphertext
    )

    grpc = (
        b"\x00"
        + len(proto_enc).to_bytes(
            4,
            "big",
        )
        + proto_enc
    )

    return grpc, ticks


def decrypt_response(
    src: bytes,
    secret: str,
) -> bytes:
    if len(src) < 5:
        raise PublicGameImportError(
            "Response too short"
        )

    if src[0] != 0:
        raise PublicGameImportError(
            "Unexpected gRPC compression flag"
        )

    grpc_len = int.from_bytes(
        src[1:5],
        "big",
    )
    payload = src[
        5:5 + grpc_len
    ]

    if len(payload) != grpc_len:
        raise PublicGameImportError(
            "Truncated gRPC response"
        )

    if len(payload) < 4:
        raise PublicGameImportError(
            "Encrypted response was too short"
        )

    header_len = int.from_bytes(
        payload[0:2],
        "little",
    )
    compressed = bool(
        payload[2] & 1
    )
    key_len = payload[3]

    if header_len != key_len + 2:
        raise PublicGameImportError(
            "Unexpected encrypted response header"
        )

    msg_key = payload[
        4:4 + key_len
    ]
    ciphertext = payload[
        4 + key_len:
    ]

    if len(ciphertext) % 16:
        raise PublicGameImportError(
            "Encrypted response was not AES aligned"
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
        raise PublicGameImportError(
            "Empty decrypted response"
        )

    pad = plain[-1]

    if not (
        1 <= pad <= 16
    ):
        raise PublicGameImportError(
            "Invalid decrypted padding"
        )

    if (
        plain[-pad:]
        != bytes([pad]) * pad
    ):
        raise PublicGameImportError(
            "Invalid decrypted padding"
        )

    plain = plain[:-pad]

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

    if len(src) < 5:
        raise PublicGameImportError(
            "Response too short"
        )

    if src[0] != 0:
        raise PublicGameImportError(
            "Unexpected gRPC compression flag"
        )

    grpc_len = int.from_bytes(
        src[1:5],
        "big",
    )
    payload = src[
        5:5 + grpc_len
    ]

    if len(payload) != grpc_len:
        raise PublicGameImportError(
            "Truncated gRPC response"
        )

    return payload


def extract_master_version(
    plain: bytes,
) -> str | None:
    raw = get_first(
        parse_fields(plain),
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
) -> list[dict]:
    rows = []

    for raw_history in get_all(
        parse_fields(plain),
        1,
        2,
    ):
        fields = parse_fields(
            raw_history
        )

        rows.append(
            {
                "gacha_name": decode_text(
                    get_first(
                        fields,
                        1,
                        2,
                    )
                ),
                "card_id": decode_text(
                    get_first(
                        fields,
                        2,
                        2,
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
) -> list[dict]:
    rows = []

    for raw_group in get_all(
        parse_fields(plain),
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
                    "gacha_id": decode_text(
                        get_first(
                            fields,
                            1,
                            2,
                        )
                    ),
                    "name": decode_text(
                        get_first(
                            fields,
                            3,
                            2,
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
        return None, None

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

    if (
        "area" in value
        and "select" in value
    ):
        return "select"
    if "select" in value:
        return "select"
    if value.startswith(
        "gacha-fixed-"
    ):
        return "guaranteed"
    if "pickup" in value:
        return "pickup"
    if "beginner" in value:
        return "beginner"
    if "collab" in value:
        return "collaboration"
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
        region_config = REGION_CONFIG[self.region]
        self.base_url = str(region_config["base_url"])
        self.bundle_id = str(region_config["bundle_id"])
        self.lang_type = str(region_config["lang_type"])
        self.credential = credential
        self.secret = secret
        self.x_as = x_as
        self.app_ad_id = app_ad_id
        self.app_version = app_version
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
            "content-type": "application/grpc+proto-enc",
            "te": "trailers",
            "grpc-accept-encoding": "identity,gzip",
            "user-agent": (
                "grpc-dotnet/2.76.0 "
                "(Mono Unity; CLR 4.0.30319.17020; "
                "netstandard2.1; arm64)"
            ),
            "x-app-version": self.app_version,
            "x-app-request-id": str(
                ticks
            ),
            "x-app-device-name": "ROG ASUS AI2201_B",
            "x-app-os-version": (
                "Android OS 14 / API-34 "
                "(UQ1A.240205.07131809/ucsm.20260713.180933)"
            ),
            "x-app-lang-type": self.lang_type,
            "x-app-os-type": "Android",
            "x-app-store-type": "GooglePlay",
            "x-app-bundle-id": self.bundle_id,
            "x-app-cpu": "x86-64 SSE3 SSE4.1 SSE4.2 AVX",
            "x-app-gpu": "Adreno (TM) 750",
            "x-app-operating-system-family": "Other",
            "x-app-device-type": "Handheld",
            "x-app-graphics-device-type": "Vulkan",
            "x-app-graphics-device-version": (
                "Vulkan 1.1.0 [592.4.0 (0x94004000)]"
            ),
            "x-app-graphics-device-vendor-id": "20803",
            "x-app-graphics-device-memory-size": "1536",
            "x-app-graphics-shader-level": "50",
            "x-app-processor-model": "x86-64 SSE3 SSE4.1 SSE4.2 AVX",
            "x-app-processor-count": "6",
            "x-app-system-memory-size": "5944",
            "x-app-screen-dpi": "480",
            "x-app-screen-height": "878",
            "x-app-screen-width": "1560",
            "x-a-ise": "False",
            "x-a-isda": "False",
            "x-a-isop": "False",
            "x-a-isr": "True",
            "x-a-s": self.x_as,
            "x-app-ad-id": self.app_ad_id,
            "grpc-timeout": "10S",
        }

        if self.master_version:
            headers[
                "x-app-master-version"
            ] = self.master_version

        if authenticated:
            if not self.auth_token:
                raise PublicGameImportError(
                    "Authenticated request without a game token"
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
            self.base_url + path,
            headers=self.headers(
                authenticated=authenticated
            ),
            content=EMPTY_GRPC,
        )

        if response.status_code != 200:
            raise PublicGameImportError(
                f"{path} failed: HTTP {response.status_code}"
            )

        if (
            not response.content
            and not allow_empty
        ):
            raise PublicGameImportError(
                f"{path} returned no data"
            )

        return bytes(
            response.content
        )

    def refresh_master_version(
        self,
    ) -> str:
        for candidate in (
            None,
            "0" * 64,
        ):
            request_headers = self.headers(
                authenticated=False
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
                response.status_code != 200
                or not response.content
            ):
                continue

            try:
                plain = decode_response_flexible(
                    bytes(
                        response.content
                    ),
                    self.secret,
                )
                value = extract_master_version(
                    plain
                )
            except Exception:
                value = None

            if value:
                self.master_version = value
                return value

        raise PublicGameImportError(
            "Could not discover the current hololive Dreams Master version."
        )

    def login(self) -> None:
        self.refresh_master_version()

        plain = protobuf_string(
            1,
            self.credential,
        )

        request, ticks = build_encrypted_request(
            plain,
            self.secret,
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
            response.status_code != 200
            or not response.content
        ):
            raise PublicGameImportError(
                "Could not authenticate this hololive Dreams account."
            )

        plain_response = decode_response_flexible(
            bytes(
                response.content
            ),
            self.secret,
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
            raise PublicGameImportError(
                "The game login returned no auth token."
            )

        self.auth_token = token

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
            plain = decode_response_flexible(
                content,
                self.secret,
            )
            return decode_user_identity(
                plain
            )
        except Exception:
            return None, None

    def gacha_list(
        self,
    ) -> list[dict]:
        try:
            content = self.empty_rpc(
                "/rpc.api.Gacha/List",
                authenticated=True,
            )
            plain = decode_response_flexible(
                content,
                self.secret,
            )
            return decode_gacha_list(
                plain
            )
        except Exception:
            return []

    def gacha_history(
        self,
    ) -> list[dict]:
        content = self.empty_rpc(
            "/rpc.api.Gacha/ListHistory",
            authenticated=True,
        )
        plain = decode_response_flexible(
            content,
            self.secret,
        )
        return decode_gacha_history(
            plain
        )


def card_metadata() -> dict[
    str,
    dict,
]:
    global _METADATA_CACHE
    global _METADATA_CACHE_AT

    now = time.monotonic()

    if (
        _METADATA_CACHE is not None
        and (
            now
            - _METADATA_CACHE_AT
        ) < _METADATA_TTL_SECONDS
    ):
        return _METADATA_CACHE

    try:
        with httpx.Client(
            timeout=30,
            follow_redirects=True,
        ) as client:
            card_response = client.get(
                MASTER_RAW_BASE
                + "/Card.json"
            )
            lang_response = client.get(
                MASTER_RAW_BASE
                + "/LangCard_Eng.json"
            )
            card_response.raise_for_status()
            lang_response.raise_for_status()
            card_rows = card_response.json()
            lang_rows = lang_response.json()
    except Exception:
        return (
            _METADATA_CACHE
            if _METADATA_CACHE is not None
            else {}
        )

    lang = {}

    for row in (
        lang_rows or []
    ):
        data = (
            row.get("data")
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
            row.get("id")
            or data.get("id")
            or ""
        ).strip()

        value = str(
            data.get("text")
            or ""
        ).strip()

        if key and value:
            lang[key] = value

    result = {}

    for row in (
        card_rows or []
    ):
        data = (
            row.get("data")
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
            row.get("id")
            or data.get("id")
            or ""
        ).strip()

        if not card_id:
            continue

        rarity_value = str(
            data.get("rarity")
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
        }

    _METADATA_CACHE = result
    _METADATA_CACHE_AT = now

    return result


def fetch_public_import_payload(
    credential: str,
) -> dict:
    credential = validate_credential(
        credential
    )

    secret = str(
        os.getenv(
            "HD_MARSHALLER_SECRET",
            "",
        )
        or ""
    ).strip()

    app_ad_id = str(
        os.getenv(
            "HD_APP_AD_ID",
            "",
        )
        or ""
    ).strip()

    if not secret or not app_ad_id:
        raise PublicGameImportError(
            "The website importer is temporarily unavailable. Please try again later."
        )

    x_as = x_as_value()

    if not x_as:
        raise PublicGameImportError(
            "The website importer is temporarily unavailable. Please try again later."
        )

    for region in REGION_ORDER:
        client = None

        try:
            client = GameClient(
                region=region,
                credential=credential,
                secret=secret,
                x_as=x_as,
                app_ad_id=app_ad_id,
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

            live_by_name = {}

            for row in client.gacha_list():
                name = str(
                    row.get("name")
                    or ""
                ).strip()

                if name:
                    live_by_name[
                        name.casefold()
                    ] = row

            history = [
                row
                for row in client.gacha_history()
                if (
                    str(
                        row.get("card_id")
                        or ""
                    ).strip()
                    and int(
                        row.get("draw_time")
                        or 0
                    ) > 0
                )
            ]

            metadata = card_metadata()

            history.sort(
                key=lambda row: (
                    int(
                        row.get("draw_time")
                        or 0
                    ),
                    str(
                        row.get("gacha_name")
                        or ""
                    ),
                    str(
                        row.get("card_id")
                        or ""
                    ),
                )
            )

            occurrence_counts = defaultdict(
                int
            )
            pulls = []

            for row in history:
                gacha_name = str(
                    row.get("gacha_name")
                    or "Unknown Gacha"
                ).strip()
                card_id = str(
                    row.get("card_id")
                    or ""
                ).strip()
                draw_time = int(
                    row.get("draw_time")
                    or 0
                )

                match = live_by_name.get(
                    gacha_name.casefold()
                )

                game_banner_id = (
                    str(
                        match.get("gacha_id")
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
                    card.get("rarity")
                    or infer_card_rarity(
                        card_id
                    )
                )

                if not rarity:
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
                        "gacha_name": gacha_name,
                        "game_banner_id": (
                            game_banner_id
                            or None
                        ),
                        "banner_type": infer_banner_type(
                            game_banner_id
                        ),
                        "card_id": card_id,
                        "item_name": (
                            card.get("name")
                            or card_id
                        ),
                        "rarity": int(
                            rarity
                        ),
                        "draw_time": draw_time,
                        "occurrence_index": (
                            occurrence_counts[
                                occurrence_key
                            ]
                        ),
                    }
                )

            if not public_user_id:
                public_user_id = (
                    "site-"
                    + hashlib.sha256(
                        (
                            region
                            + "|"
                            + credential
                        ).encode(
                            "utf-8"
                        )
                    ).hexdigest()[:24]
                )

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
                    "public-powershell-v1"
                ),
                "pulls": pulls,
            }

        except Exception:
            pass

        finally:
            if client is not None:
                client.close()

    raise PublicGameImportError(
        "Could not authenticate this hololive Dreams account. "
        "Launch the game, make sure the account is logged in, and try again."
    )
