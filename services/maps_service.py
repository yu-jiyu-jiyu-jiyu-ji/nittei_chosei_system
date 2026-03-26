"""Google Maps（Distance Matrix）による移動時間取得."""

from __future__ import annotations

import os
from typing import Dict, Optional, Tuple

# googlemaps はオプション扱い（未インストール時は requests で代替）
try:
    import googlemaps
except ImportError:
    googlemaps = None  # type: ignore


def maps_api_key_configured() -> bool:
    return bool(os.environ.get("GOOGLE_MAPS_API_KEY", "").strip())


# 同一検索セッション内での Distance Matrix 重複呼び出しを避ける（候補検索が劇的に速くなる）
_DM_CACHE: Dict[Tuple[str, str, str], Optional[float]] = {}
_DM_CACHE_MAX = 8000

# Client を毎回 new すると TLS 接続・認証のオーバーヘッドが大きい（検索が著しく遅くなる）
_gmaps_client: Optional[object] = None


def _get_gmaps_client():
    global _gmaps_client
    if googlemaps is None:
        return None
    if _gmaps_client is None:
        key = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
        if key:
            _gmaps_client = googlemaps.Client(key=key)
    return _gmaps_client


def travel_duration_minutes(
    origin_address: str,
    destination_address: str,
    *,
    mode: str = "driving",
) -> Optional[float]:
    """出発地・目的地の住所から、自動車移動の所要時間（分）を取得.

    API キー未設定・失敗時は None。
    """
    key = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
    if not key or not origin_address.strip() or not destination_address.strip():
        return None
    o = origin_address.strip()
    d = destination_address.strip()
    cache_key = (o, d, mode)
    if cache_key in _DM_CACHE:
        return _DM_CACHE[cache_key]
    try:
        if googlemaps is not None:
            client = _get_gmaps_client()
            if client is None:
                _DM_CACHE[cache_key] = None
                return None
            result = client.distance_matrix(
                origins=[origin_address.strip()],
                destinations=[destination_address.strip()],
                mode=mode,
                units="metric",
                language="ja",
            )
        else:
            out = _distance_matrix_requests(
                key, origin_address.strip(), destination_address.strip(), mode
            )
            _DM_CACHE[cache_key] = out
            return out
        row = result.get("rows", [{}])[0]
        el = row.get("elements", [{}])[0]
        if el.get("status") != "OK":
            _DM_CACHE[cache_key] = None
            return None
        sec = el.get("duration", {}).get("value")
        if sec is None:
            _DM_CACHE[cache_key] = None
            return None
        out = float(sec) / 60.0
        _dm_cache_maybe_trim()
        _DM_CACHE[cache_key] = out
        return out
    except Exception:
        _dm_cache_maybe_trim()
        _DM_CACHE[cache_key] = None
        return None


def _dm_cache_maybe_trim() -> None:
    if len(_DM_CACHE) >= _DM_CACHE_MAX:
        _DM_CACHE.clear()


def _distance_matrix_requests(
    key: str, origin: str, dest: str, mode: str
) -> Optional[float]:
    import urllib.parse

    import requests

    base = "https://maps.googleapis.com/maps/api/distancematrix/json"
    params = {
        "origins": origin,
        "destinations": dest,
        "mode": mode,
        "key": key,
        "language": "ja",
    }
    r = requests.get(base, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    row = data.get("rows", [{}])[0]
    el = row.get("elements", [{}])[0]
    if el.get("status") != "OK":
        return None
    sec = el.get("duration", {}).get("value")
    if sec is None:
        return None
    return float(sec) / 60.0
