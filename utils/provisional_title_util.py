"""空き確定時の仮タイトル提案（既存予定の踏襲 / 書式フォールバック）."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

from services.setting_service import (
    PROVISIONAL_TITLE_MODE_EXISTING,
    PROVISIONAL_TITLE_MODE_FORMAT,
)

_TZ = ZoneInfo("Asia/Tokyo")
_DEFAULT_FORMAT = "空き確保 YYYY/MM/DD HH:MM"


def format_provisional_title(template: str, start_at: datetime) -> str:
    """書式テンプレートを日時で展開する（トークン: YYYY / MM / DD / HH:MM）."""
    dt = start_at
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_TZ)
    else:
        dt = dt.astimezone(_TZ)
    text = str(template or "").strip() or _DEFAULT_FORMAT
    # HH:MM を先に置換（単独の MM と衝突しない）
    text = text.replace("HH:MM", dt.strftime("%H:%M"))
    text = text.replace("YYYY", f"{dt.year:04d}")
    text = text.replace("MM", f"{dt.month:02d}")
    text = text.replace("DD", f"{dt.day:02d}")
    return text.strip() or _DEFAULT_FORMAT


def normalize_project_name_from_title(title: str) -> str:
    """カレンダー風タイトルから案件名を取り出す（[現場] 接頭辞は除去）."""
    t = str(title or "").strip()
    if t.startswith("[現場]"):
        t = t[len("[現場]") :].strip()
    return t or str(title or "").strip()


def _is_usable_field_summary(summary: str) -> bool:
    s = str(summary or "").strip()
    if not s or s in ("（無題）", "無題"):
        return False
    if s.startswith("[移動]"):
        return False
    return True


def suggest_title_from_adjacent(
    candidate: Optional[Dict[str, Any]],
    *,
    start_at: datetime,
    settings: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str]:
    """仮タイトルと出典メモを返す.

    Returns:
        (title, source_note)
    """
    cfg = settings or {}
    mode = str(cfg.get("provisional_title_mode") or PROVISIONAL_TITLE_MODE_EXISTING).strip()
    if mode not in (PROVISIONAL_TITLE_MODE_EXISTING, PROVISIONAL_TITLE_MODE_FORMAT):
        mode = PROVISIONAL_TITLE_MODE_EXISTING
    fmt = str(cfg.get("provisional_title_format") or _DEFAULT_FORMAT).strip() or _DEFAULT_FORMAT
    fallback = format_provisional_title(fmt, start_at)

    if mode == PROVISIONAL_TITLE_MODE_FORMAT:
        return fallback, "設定の書式"

    adj = (candidate or {}).get("worker_adjacent_events") or {}
    if not isinstance(adj, dict):
        return fallback, "設定の書式（参考予定なし）"

    worker_ids = list((candidate or {}).get("worker_ids") or [])
    # worker_ids 順 → 直前優先 → 直後
    for wid in worker_ids:
        info = adj.get(str(wid)) or {}
        if not isinstance(info, dict):
            continue
        for key in ("prev", "next"):
            ev = info.get(key)
            if not isinstance(ev, dict):
                continue
            summary = str(ev.get("summary") or "").strip()
            if _is_usable_field_summary(summary):
                return summary, "直前・直後の既存予定を参考"

    # worker_ids が空でも map 全体を見る
    for info in adj.values():
        if not isinstance(info, dict):
            continue
        for key in ("prev", "next"):
            ev = info.get(key)
            if not isinstance(ev, dict):
                continue
            summary = str(ev.get("summary") or "").strip()
            if _is_usable_field_summary(summary):
                return summary, "直前・直後の既存予定を参考"

    return fallback, "設定の書式（参考予定なし）"
