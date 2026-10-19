from __future__ import annotations

import asyncio
from datetime import datetime
import io
import re
import tempfile
import time
from pathlib import Path
from typing import Iterable

import httpx
from PIL import Image

from .config import get_settings


CATALOG_MAX_AGE_SECONDS = 30 * 60
ASSET_DOWNLOAD_TIMEOUT_SECONDS = 120.0
MAX_EXTRACTED_IMAGE_BYTES = 20 * 1024 * 1024

_SAFE_NAME_RE = re.compile(r"[^a-zA-Z0-9_.-]+")
_GACHA_PICKUP_RE = re.compile(
    r"^gacha-pickup-(normal|select)-(\d{6})$",
    re.IGNORECASE,
)


_KNOWN_BANNER_IDS = {
    "take your pick! dreamy deep blue gacha": (
        "gacha-pickup-select-260929"
    ),
    "dreamy deep blue gacha": (
        "gacha-pickup-normal-260929"
    ),
    "take your pick! seeking the summer cool gacha": (
        "gacha-pickup-select-260919"
    ),
    "seeking the summer cool gacha": (
        "gacha-pickup-normal-260919"
    ),
    "take your pick! sea bridge gacha": (
        "gacha-pickup-select-261007"
    ),
    "sea bridge gacha": (
        "gacha-pickup-normal-261007"
    ),
    "new area celebration! take your pick! ★5 member gacha": (
        "gacha-area03-select-260929"
    ),
    "new area celebration! one ★5 member guaranteed gacha": (
        "gacha-fixed-260929"
    ),
    "holodori gacha": (
        "gacha-common-normal-001"
    ),
}


# Exact name mappings above remain authoritative for campaigns where multiple
# gachas overlap and a date-only heuristic would be ambiguous.
_HISTORICAL_BANNER_MAX_PREVIOUS_DAYS = 45
_HISTORICAL_BANNER_MAX_FUTURE_DAYS = 7


def _banner_family(
    *,
    game_banner_id: str,
    banner_name: str,
    banner_type: str,
) -> str:
    value = str(
        game_banner_id
        or ""
    ).strip().casefold()
    name = str(
        banner_name
        or ""
    ).strip().casefold()
    kind = str(
        banner_type
        or ""
    ).strip().casefold()

    # Area celebration selection banners need to be detected before the
    # generic "take your pick" rule.
    if (
        "new area celebration" in name
        or value.startswith(
            "gacha-area03"
        )
    ):
        return "area_select"

    if (
        "guaranteed" in name
        or value.startswith(
            "gacha-fixed-"
        )
        or kind == "guaranteed"
    ):
        return "guaranteed"

    if (
        name == "holodori gacha"
        or value.startswith(
            "gacha-common-normal"
        )
        or kind == "standard"
    ):
        return "standard"

    if (
        "take your pick" in name
        or "pickup-select" in value
        or kind == "select"
    ):
        return "pickup_select"

    if (
        "pickup-normal" in value
        or (
            "pickup" in value
            and "select" not in value
        )
        or kind == "pickup"
    ):
        return "pickup_normal"

    if (
        "beginner" in value
        or kind == "beginner"
    ):
        return "beginner"

    if (
        "collab" in value
        or kind == "collaboration"
    ):
        return "collaboration"

    return "unknown"


def _campaign_date_from_asset_name(
    asset_name: str,
    *,
    family: str,
) -> str | None:
    lowered = str(
        asset_name
        or ""
    ).casefold()

    if family in {
        "pickup_select",
        "pickup_normal",
    }:
        prefixes = (
            "img_banner_full_gacha-pickup-",
            "img_banner_gacha-pickup-",
        )
    elif family == "guaranteed":
        prefixes = (
            "img_banner_full_gacha-fixed-",
            "img_banner_gacha-fixed-",
        )
    elif family == "area_select":
        prefixes = (
            "img_banner_full_gacha-area03",
            "img_banner_gacha-area03",
        )
    else:
        return None

    if not any(
        prefix in lowered
        for prefix in prefixes
    ):
        return None

    match = re.search(
        r"(?<!\\d)(\\d{6})(?!\\d)",
        lowered,
    )

    if not match:
        return None

    try:
        datetime.strptime(
            match.group(1),
            "%y%m%d",
        )
    except ValueError:
        return None

    return match.group(1)


def _choose_historical_campaign_date(
    date_codes: Iterable[str],
    *,
    first_pulled_at,
) -> str | None:
    if first_pulled_at is None:
        return None

    try:
        target_date = first_pulled_at.date()
    except AttributeError:
        return None

    parsed = []

    for code in set(
        str(value)
        for value in date_codes
        if value
    ):
        try:
            candidate_date = datetime.strptime(
                code,
                "%y%m%d",
            ).date()
        except ValueError:
            continue

        parsed.append(
            (
                candidate_date,
                code,
            )
        )

    if not parsed:
        return None

    previous = sorted(
        (
            item
            for item in parsed
            if item[0] <= target_date
        ),
        reverse=True,
    )

    if previous:
        candidate_date, code = previous[0]
        delta = (
            target_date
            - candidate_date
        ).days

        if (
            delta
            <= _HISTORICAL_BANNER_MAX_PREVIOUS_DAYS
        ):
            return code

    future = sorted(
        item
        for item in parsed
        if item[0] > target_date
    )

    if future:
        candidate_date, code = future[0]
        delta = (
            candidate_date
            - target_date
        ).days

        if (
            delta
            <= _HISTORICAL_BANNER_MAX_FUTURE_DAYS
        ):
            return code

    return None


def _historical_game_banner_id(
    *,
    family: str,
    date_code: str,
) -> str | None:
    if family == "pickup_select":
        return (
            "gacha-pickup-select-"
            + date_code
        )

    if family == "pickup_normal":
        return (
            "gacha-pickup-normal-"
            + date_code
        )

    if family == "guaranteed":
        return (
            "gacha-fixed-"
            + date_code
        )

    if family == "area_select":
        return (
            "gacha-area03-select-"
            + date_code
        )

    return None



def _safe_name(value: str) -> str:
    cleaned = _SAFE_NAME_RE.sub(
        "_",
        str(value or ""),
    ).strip("._")
    return cleaned or "asset"


def _card_asset_token(card_id: str) -> str:
    value = str(card_id or "").strip()
    if value.casefold().startswith("card-"):
        return value[5:]
    return value


def _banner_candidates(
    game_banner_id: str,
) -> tuple[str, ...]:
    value = str(
        game_banner_id
        or ""
    ).strip()

    if not value:
        return ()

    candidates = [
        "img_banner_full_"
        + value
        + "_lang-eng",
        "img_banner_full_"
        + value,
    ]

    match = _GACHA_PICKUP_RE.fullmatch(
        value
    )

    if match:
        date_code = match.group(2)

        # Normal and member-select pickup banners share the same full
        # campaign artwork asset.
        base = (
            "img_banner_full_gacha-pickup-"
            + date_code
        )

        candidates = [
            base + "_lang-eng",
            base,
            *candidates,
        ]

    ordered = []
    seen = set()

    for candidate in candidates:
        key = candidate.casefold()
        if not candidate or key in seen:
            continue
        seen.add(key)
        ordered.append(candidate)

    return tuple(ordered)


def _score_card_catalog_name(
    name: str,
    *,
    card_id: str,
    asset_token: str,
) -> tuple[int, int, str]:
    lowered = str(name or "").casefold()
    card_lower = str(card_id or "").casefold()
    token_lower = str(asset_token or "").casefold()

    score = 0

    if card_lower and card_lower in lowered:
        score += 100
    if token_lower and token_lower in lowered:
        score += 80

    for keyword, points in (
        ("card", 35),
        ("img", 24),
        ("image", 20),
        ("icon", 16),
        ("thumb", 14),
        ("jacket", 8),
    ):
        if keyword in lowered:
            score += points

    for keyword, penalty in (
        ("frame", 35),
        ("skill", 30),
        ("effect", 20),
        ("badge", 14),
        ("mask", 12),
        ("sd_", 10),
    ):
        if keyword in lowered:
            score -= penalty

    # Prefer concise names when scores tie.
    return (
        score,
        -len(lowered),
        lowered,
    )


class AssetService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._catalog = None
        self._catalog_loaded_monotonic = 0.0
        self._bundle_index = {}
        self._resource_index = {}
        self._bundle_lower = {}
        self._resource_lower = {}
        self._catalog_lock = asyncio.Lock()
        self._missing_card_refresh_lock = asyncio.Lock()
        self._last_missing_card_refresh = 0.0
        self._asset_locks: dict[str, asyncio.Lock] = {}

    @property
    def cache_root(self) -> Path:
        root = Path(
            self.settings.asset_cache_dir
        )
        root.mkdir(
            parents=True,
            exist_ok=True,
        )
        return root

    @property
    def catalog_path(self) -> Path:
        path = Path(
            self.settings.asset_catalog_path
        )
        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        return path

    @property
    def image_root(self) -> Path:
        path = self.cache_root / "images"
        path.mkdir(
            parents=True,
            exist_ok=True,
        )
        return path

    def _lock_for(
        self,
        key: str,
    ) -> asyncio.Lock:
        lock = self._asset_locks.get(
            key
        )
        if lock is None:
            lock = asyncio.Lock()
            self._asset_locks[key] = lock
        return lock

    async def _load_catalog(
        self,
        *,
        force: bool = False,
    ):
        async with self._catalog_lock:
            now = time.monotonic()

            if (
                not force
                and self._catalog is not None
                and (
                    now
                    - self._catalog_loaded_monotonic
                ) < CATALOG_MAX_AGE_SECONDS
            ):
                return self._catalog

            catalog_obj = await asyncio.to_thread(
                self._load_catalog_sync,
                force,
            )

            self._catalog = catalog_obj
            self._catalog_loaded_monotonic = (
                time.monotonic()
            )

            self._bundle_index = {
                str(entry.name): entry
                for entry
                in (
                    catalog_obj.assetBundles
                    or []
                )
            }
            self._resource_index = {
                str(entry.name): entry
                for entry
                in (
                    catalog_obj.resources
                    or []
                )
            }
            self._bundle_lower = {
                key.casefold(): (
                    key,
                    entry,
                )
                for key, entry
                in self._bundle_index.items()
            }
            self._resource_lower = {
                key.casefold(): (
                    key,
                    entry,
                )
                for key, entry
                in self._resource_index.items()
            }

            return catalog_obj

    def _load_catalog_sync(
        self,
        force: bool,
    ):
        from holodori_asset_tools import (
            catalog,
        )

        if force:
            try:
                catalog.appver.cache_clear()
            except AttributeError:
                pass

            catalog_obj = catalog.fetch()
            catalog_obj.save(
                self.catalog_path
            )
            return catalog_obj

        return catalog.get(
            self.catalog_path
        )

    async def status(self) -> dict:
        try:
            catalog_obj = await self._load_catalog()
        except ModuleNotFoundError:
            return {
                "available": False,
                "installed": False,
                "error": (
                    "holodori-asset-tools is not installed "
                    "in this virtual environment"
                ),
            }
        except Exception as exc:
            return {
                "available": False,
                "installed": True,
                "error": (
                    f"{type(exc).__name__}: {exc}"
                ),
            }

        try:
            import holodori_asset_tools

            module_path = str(
                Path(
                    holodori_asset_tools.__file__
                ).resolve()
            )
        except Exception:
            module_path = None

        return {
            "available": True,
            "installed": True,
            "module_path": module_path,
            "catalog_path": str(
                self.catalog_path.resolve()
            ),
            "catalog_revision": getattr(
                catalog_obj,
                "revisionId",
                None,
            ),
            "asset_bundles": len(
                self._bundle_index
            ),
            "resources": len(
                self._resource_index
            ),
            "cached_images": sum(
                1
                for path
                in self.image_root.glob(
                    "*.png"
                )
                if path.is_file()
            ),
        }

    async def refresh_catalog(
        self,
    ) -> dict:
        await self._load_catalog(
            force=True
        )
        return await self.status()

    async def search(
        self,
        query: str,
        *,
        limit: int = 50,
    ) -> list[dict]:
        await self._load_catalog()

        normalized = str(
            query or ""
        ).strip().casefold()
        if not normalized:
            return []

        result = []

        for group, index in (
            (
                "assetbundles",
                self._bundle_index,
            ),
            (
                "resources",
                self._resource_index,
            ),
        ):
            for name, entry in index.items():
                if normalized not in name.casefold():
                    continue

                result.append(
                    {
                        "group": group,
                        "name": name,
                        "size": int(
                            getattr(
                                entry,
                                "size",
                                0,
                            )
                            or 0
                        ),
                    }
                )

                if len(result) >= limit:
                    return result

        return result

    def _exact_asset(
        self,
        candidates: Iterable[str],
    ):
        for candidate in candidates:
            lowered = str(
                candidate
                or ""
            ).casefold()

            if not lowered:
                continue

            found = self._bundle_lower.get(
                lowered
            )
            if found is not None:
                name, entry = found
                return (
                    "assetbundles",
                    name,
                    entry,
                )

            found = self._resource_lower.get(
                lowered
            )
            if found is not None:
                name, entry = found
                return (
                    "resources",
                    name,
                    entry,
                )

        return None

    async def resolve_banner(
        self,
        game_banner_id: str,
    ):
        await self._load_catalog()

        candidates = _banner_candidates(
            game_banner_id
        )
        if not candidates:
            return None

        return self._exact_asset(
            candidates
        )


    async def resolve_banner_hinted(
        self,
        *,
        game_banner_id: str,
        banner_name: str = "",
        banner_type: str = "",
        first_pulled_at=None,
    ):
        value = str(
            game_banner_id
            or ""
        ).strip().casefold()
        name = str(
            banner_name
            or ""
        ).strip().casefold()

        # Historical Gii Murin rows may only have a synthetic banner ID.
        # For known campaigns, resolve the exact original game banner ID
        # from the official banner name instead of guessing by date.
        mapped_id = _KNOWN_BANNER_IDS.get(
            name
        )

        if mapped_id:
            direct = await self.resolve_banner(
                mapped_id
            )
            if direct is not None:
                return direct

        direct = await self.resolve_banner(
            game_banner_id
        )
        if direct is not None:
            return direct

        await self._load_catalog()

        family = _banner_family(
            game_banner_id=(
                game_banner_id
            ),
            banner_name=(
                banner_name
            ),
            banner_type=(
                banner_type
            ),
        )

        # Permanent standard banner has a stable exact ID.
        if family == "standard":
            standard = await self.resolve_banner(
                "gacha-common-normal-001"
            )
            if standard is not None:
                return standard

        # For historical synthetic IDs, first narrow the catalog to the
        # correct gacha family, then derive the closest plausible campaign
        # date. This is safer than selecting any nearby gacha artwork.
        date_codes = []

        for index in (
            self._bundle_index,
            self._resource_index,
        ):
            for asset_name in index:
                code = (
                    _campaign_date_from_asset_name(
                        asset_name,
                        family=family,
                    )
                )

                if code:
                    date_codes.append(
                        code
                    )

        campaign_date = (
            _choose_historical_campaign_date(
                date_codes,
                first_pulled_at=(
                    first_pulled_at
                ),
            )
        )

        if campaign_date:
            historical_id = (
                _historical_game_banner_id(
                    family=family,
                    date_code=(
                        campaign_date
                    ),
                )
            )

            if historical_id:
                historical = (
                    await self.resolve_banner(
                        historical_id
                    )
                )

                if historical is not None:
                    return historical

        prefixes = []

        if family == "guaranteed":
            prefixes.extend(
                (
                    "img_banner_full_gacha-fixed-",
                    "img_banner_gacha-fixed-",
                )
            )

        elif family == "area_select":
            prefixes.extend(
                (
                    "img_banner_full_gacha-area03",
                    "img_banner_gacha-area03",
                )
            )

        elif family == "standard":
            prefixes.extend(
                (
                    "img_banner_full_gacha-common",
                    "img_banner_gacha-common",
                )
            )

        elif family in {
            "pickup_select",
            "pickup_normal",
        }:
            prefixes.extend(
                (
                    "img_banner_full_gacha-pickup-",
                    "img_banner_gacha-pickup-",
                )
            )

        if not prefixes:
            return None

        target_date = None
        if first_pulled_at is not None:
            try:
                target_date = (
                    first_pulled_at.date()
                )
            except AttributeError:
                target_date = None

        matches = []

        for group, index in (
            (
                "assetbundles",
                self._bundle_index,
            ),
            (
                "resources",
                self._resource_index,
            ),
        ):
            for asset_name, entry in index.items():
                lowered = str(
                    asset_name
                    or ""
                ).casefold()

                if not any(
                    prefix in lowered
                    for prefix in prefixes
                ):
                    continue

                score = 0

                if "img_banner_full_" in lowered:
                    score += 2_000

                if "_lang-eng" in lowered:
                    score += 500

                date_match = re.search(
                    r"(?<!\\d)(\\d{6})(?!\\d)",
                    lowered,
                )

                if (
                    target_date is not None
                    and date_match is not None
                ):
                    try:
                        asset_date = datetime.strptime(
                            date_match.group(1),
                            "%y%m%d",
                        ).date()
                    except ValueError:
                        asset_date = None

                    if asset_date is not None:
                        delta_days = (
                            target_date
                            - asset_date
                        ).days

                        if delta_days >= 0:
                            score += (
                                20_000
                                - min(
                                    delta_days,
                                    10_000,
                                )
                            )
                        else:
                            score += (
                                10_000
                                - min(
                                    abs(
                                        delta_days
                                    ),
                                    9_000,
                                )
                            )

                matches.append(
                    (
                        score,
                        -len(
                            lowered
                        ),
                        lowered,
                        group,
                        asset_name,
                        entry,
                    )
                )

        if not matches:
            return None

        matches.sort(
            reverse=True
        )

        (
            _score,
            _length_score,
            _lowered,
            group,
            asset_name,
            entry,
        ) = matches[0]

        return (
            group,
            asset_name,
            entry,
        )

    async def resolve_card(
        self,
        card_id: str,
    ):
        await self._load_catalog()

        card_id = str(
            card_id or ""
        ).strip()
        asset_token = _card_asset_token(
            card_id
        )

        if not asset_token:
            return None

        # Try likely explicit naming forms first. The token search below is
        # authoritative fallback and keeps this resilient if the game changes
        # prefixes while retaining Card.assetId.
        explicit = (
            f"img_card_{asset_token}",
            f"img_card_full_{asset_token}",
            f"img_card_thumb_{asset_token}",
            f"img_card_thumbnail_{asset_token}",
            f"img_card_icon_{asset_token}",
        )
        found = self._exact_asset(
            explicit
        )
        if found is not None:
            return found

        matches = []

        token_lower = asset_token.casefold()
        card_lower = card_id.casefold()

        for group, index in (
            (
                "assetbundles",
                self._bundle_index,
            ),
            (
                "resources",
                self._resource_index,
            ),
        ):
            for name, entry in index.items():
                lowered = name.casefold()

                if (
                    token_lower not in lowered
                    and card_lower not in lowered
                ):
                    continue

                matches.append(
                    (
                        _score_card_catalog_name(
                            name,
                            card_id=card_id,
                            asset_token=asset_token,
                        ),
                        group,
                        name,
                        entry,
                    )
                )

        if not matches:
            return None

        matches.sort(
            key=lambda item: item[0],
            reverse=True,
        )

        _score, group, name, entry = (
            matches[0]
        )

        return (
            group,
            name,
            entry,
        )

    async def card_matches(
        self,
        card_id: str,
        *,
        limit: int = 30,
    ) -> list[dict]:
        await self._load_catalog()

        card_id = str(
            card_id or ""
        ).strip()
        asset_token = _card_asset_token(
            card_id
        )

        if not asset_token:
            return []

        matches = []

        for group, index in (
            (
                "assetbundles",
                self._bundle_index,
            ),
            (
                "resources",
                self._resource_index,
            ),
        ):
            for name, entry in index.items():
                lowered = name.casefold()

                if (
                    asset_token.casefold()
                    not in lowered
                    and card_id.casefold()
                    not in lowered
                ):
                    continue

                score = (
                    _score_card_catalog_name(
                        name,
                        card_id=card_id,
                        asset_token=asset_token,
                    )[0]
                )

                matches.append(
                    {
                        "score": score,
                        "group": group,
                        "name": name,
                        "size": int(
                            getattr(
                                entry,
                                "size",
                                0,
                            )
                            or 0
                        ),
                    }
                )

        matches.sort(
            key=lambda item: (
                item["score"],
                item["size"],
                item["name"],
            ),
            reverse=True,
        )

        return matches[:limit]

    async def get_banner_image(
        self,
        game_banner_id: str,
    ) -> Path | None:
        resolved = await self.resolve_banner(
            game_banner_id
        )
        if resolved is None:
            return None

        return await self._cached_image(
            "banner",
            resolved,
            max_size=(1200, 500),
        )


    async def get_banner_image_hinted(
        self,
        *,
        game_banner_id: str,
        banner_name: str = "",
        banner_type: str = "",
        first_pulled_at=None,
    ) -> Path | None:
        resolved = await self.resolve_banner_hinted(
            game_banner_id=(
                game_banner_id
            ),
            banner_name=(
                banner_name
            ),
            banner_type=(
                banner_type
            ),
            first_pulled_at=(
                first_pulled_at
            ),
        )

        if resolved is None:
            return None

        return await self._cached_image(
            "banner",
            resolved,
            max_size=(1200, 500),
        )

    async def get_card_image(
        self,
        card_id: str,
    ) -> Path | None:
        resolved = await self.resolve_card(
            card_id
        )
        if resolved is None:
            # New cards may not exist in a previously downloaded Octo
            # catalog. Try one forced refresh, throttled across requests
            # so missing/invalid IDs cannot hammer the upstream service.
            async with self._missing_card_refresh_lock:
                now = time.monotonic()
                if (
                    now - self._last_missing_card_refresh
                    >= 10 * 60
                ):
                    self._last_missing_card_refresh = now
                    try:
                        await self._load_catalog(force=True)
                    except Exception:
                        # The previous catalog remains available when
                        # the upstream catalog is temporarily unavailable.
                        pass
                resolved = await self.resolve_card(card_id)
        if resolved is None:
            return None

        return await self._cached_image(
            "card",
            resolved,
            max_size=(512, 512),
        )

    async def _cached_image(
        self,
        prefix: str,
        resolved,
        *,
        max_size: tuple[int, int],
    ) -> Path:
        group, asset_name, entry = (
            resolved
        )

        target = (
            self.image_root
            / (
                f"{prefix}__"
                f"{_safe_name(asset_name)}"
                ".png"
            )
        )

        if target.exists():
            return target

        lock = self._lock_for(
            str(target)
        )

        async with lock:
            if target.exists():
                return target

            image_bytes = await asyncio.to_thread(
                self._download_extract_image_sync,
                group,
                entry,
                max_size,
            )

            if len(
                image_bytes
            ) > MAX_EXTRACTED_IMAGE_BYTES:
                raise RuntimeError(
                    "Extracted image is larger than "
                    f"{MAX_EXTRACTED_IMAGE_BYTES} bytes"
                )

            target.parent.mkdir(
                parents=True,
                exist_ok=True,
            )
            temp = target.with_suffix(
                ".tmp"
            )
            temp.write_bytes(
                image_bytes
            )
            temp.replace(
                target
            )

        return target

    @staticmethod
    def _download_extract_image_sync(
        group,
        entry,
        max_size: tuple[int, int],
    ) -> bytes:
        from holodori_asset_tools import (
            crypto,
        )
        from holodori_asset_tools.entrypoint.extract import (
            AssetBundle,
        )

        with httpx.Client(
            http2=True,
            timeout=ASSET_DOWNLOAD_TIMEOUT_SECONDS,
            follow_redirects=True,
        ) as client:
            response = client.get(
                entry.url
            )
            response.raise_for_status()
            raw = bytes(
                response.content
            )

        asset_name = str(
            entry.name
            or "asset"
        )

        if group == "resources":
            plain = crypto.decrypt(
                raw,
                asset_name,
            )

            image = Image.open(
                io.BytesIO(
                    plain
                )
            )
            image.load()
            return AssetService._encode_png(
                image,
                max_size,
            )

        with tempfile.TemporaryDirectory(
            prefix="holodori-gacha-art-"
        ) as tmp:
            path = (
                Path(tmp)
                / asset_name
            )
            path.write_bytes(
                raw
            )

            env = AssetBundle(
                path,
                asset_name,
            ).load()

            objects = [
                obj
                for obj in env.objects
                if obj.type.name
                in (
                    "Sprite",
                    "Texture2D",
                )
            ]

            decoded = []
            sprite_names = set()

            for obj in objects:
                if (
                    obj.type.name
                    != "Sprite"
                ):
                    continue

                data = obj.read()
                name = str(
                    getattr(
                        data,
                        "m_Name",
                        "",
                    )
                    or "sprite"
                )
                sprite_names.add(
                    name
                )
                image = data.image
                decoded.append(
                    (
                        int(
                            image.width
                        )
                        * int(
                            image.height
                        ),
                        name,
                        image,
                    )
                )

            for obj in objects:
                if (
                    obj.type.name
                    != "Texture2D"
                ):
                    continue

                data = obj.read()
                name = str(
                    getattr(
                        data,
                        "m_Name",
                        "",
                    )
                    or "texture"
                )

                if name in sprite_names:
                    continue

                image = data.image
                decoded.append(
                    (
                        int(
                            image.width
                        )
                        * int(
                            image.height
                        ),
                        name,
                        image,
                    )
                )

            if not decoded:
                raise RuntimeError(
                    "Asset bundle "
                    f"{asset_name} "
                    "contained no image"
                )

            decoded.sort(
                key=lambda item: item[0],
                reverse=True,
            )

            _area, _name, image = (
                decoded[0]
            )

            return AssetService._encode_png(
                image,
                max_size,
            )

    @staticmethod
    def _encode_png(
        image,
        max_size: tuple[int, int],
    ) -> bytes:
        if image.mode not in (
            "RGB",
            "RGBA",
        ):
            image = image.convert(
                "RGBA"
            )

        image.thumbnail(
            max_size,
            Image.Resampling.LANCZOS,
        )

        output = io.BytesIO()
        image.save(
            output,
            format="PNG",
            optimize=True,
        )
        return output.getvalue()


asset_service = AssetService()
