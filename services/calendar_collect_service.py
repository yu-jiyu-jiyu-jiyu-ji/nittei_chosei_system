"""職人カレンダー予定の収集（案件登録支援）."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from google.oauth2.credentials import Credentials

from services.calendar_service import event_time_bounds
from services.candidate_search_service import (
    TZ,
    _calendar_owner_email,
    _fetch_time_range_for_week_offsets,
    _parallel_fetch_events_by_calendar_id_with_errors,
    _worker_credentials,
    sunday_week_containing,
)

__all__ = [
    "collect_worker_calendar_rows_for_week",
    "event_visible_for_collect",
    "format_member_display_names",
    "format_collect_event_times",
    "sunday_week_containing",
]


def _normalize_calendar_email(email: Optional[str]) -> str:
    return str(email or "").strip().casefold()


def event_visible_for_collect(
    ev: Dict[str, Any],
    *,
    owner_email: Optional[str] = None,
) -> bool:
    """収集一覧に載せる予定か（キャンセル・辞退は除外）."""
    if str(ev.get("status") or "").strip().lower() == "cancelled":
        return False

    owner = _normalize_calendar_email(owner_email)
    if not owner:
        return True

    attendees = ev.get("attendees")
    if not isinstance(attendees, list):
        return True

    for att in attendees:
        if not isinstance(att, dict):
            continue
        email = _normalize_calendar_email(att.get("email"))
        if email and email == owner:
            status = str(att.get("responseStatus") or "").strip().lower()
            if status == "declined":
                return False
    return True


def format_member_display_names(ev: Dict[str, Any]) -> str:
    """参加者の表示名のみを列挙する."""
    names: List[str] = []
    seen: Set[str] = set()

    def _add(name: str) -> None:
        n = str(name or "").strip()
        if not n or n in seen:
            return
        seen.add(n)
        names.append(n)

    organizer = ev.get("organizer") if isinstance(ev.get("organizer"), dict) else {}
    if organizer.get("displayName"):
        _add(str(organizer["displayName"]))
    elif organizer.get("email"):
        _add(str(organizer["email"]).split("@")[0])

    attendees = ev.get("attendees")
    if isinstance(attendees, list):
        for att in attendees:
            if not isinstance(att, dict):
                continue
            if att.get("displayName"):
                _add(str(att["displayName"]))
            elif att.get("email"):
                _add(str(att["email"]).split("@")[0])

    return "、".join(names)


def format_collect_event_times(ev: Dict[str, Any]) -> Tuple[str, str, str]:
    """日付・開始・終了の表示文字列（終日は「終日」）."""
    bounds = event_time_bounds(ev)
    if not bounds:
        return "", "", ""
    start_at, end_at = bounds
    if start_at.tzinfo is None:
        start_at = start_at.replace(tzinfo=timezone.utc)
    if end_at.tzinfo is None:
        end_at = end_at.replace(tzinfo=timezone.utc)
    start_at = start_at.astimezone(TZ)
    end_at = end_at.astimezone(TZ)

    start_raw = ev.get("start") if isinstance(ev.get("start"), dict) else {}
    is_all_day = "date" in start_raw and "dateTime" not in start_raw
    date_s = start_at.strftime("%Y/%m/%d")
    if is_all_day:
        return date_s, "終日", "終日"
    return date_s, start_at.strftime("%H:%M"), end_at.strftime("%H:%M")


def _event_date_in_week(ev: Dict[str, Any], week_start: date) -> bool:
    bounds = event_time_bounds(ev)
    if not bounds:
        return False
    start_at, _ = bounds
    if start_at.tzinfo is None:
        start_at = start_at.replace(tzinfo=timezone.utc)
    event_date = start_at.astimezone(TZ).date()
    week_end = week_start + timedelta(days=6)
    return week_start <= event_date <= week_end


def _events_to_collect_rows(
    events: List[Dict[str, Any]],
    *,
    week_start: date,
    worker_label: str,
    calendar_id: str,
    owner_email: Optional[str],
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for ev in events:
        if not event_visible_for_collect(ev, owner_email=owner_email):
            continue
        if not _event_date_in_week(ev, week_start):
            continue
        bounds = event_time_bounds(ev)
        if not bounds:
            continue
        start_at, _ = bounds
        if start_at.tzinfo is None:
            start_at = start_at.replace(tzinfo=timezone.utc)
        date_s, start_s, end_s = format_collect_event_times(ev)
        event_id = str(ev.get("id") or "")
        title = str(ev.get("summary") or "（無題）").strip()
        location = str(ev.get("location") or "").strip()
        rows.append(
            {
                "row_key": f"{calendar_id}:{event_id}",
                "event_id": event_id,
                "calendar_id": calendar_id,
                "worker_label": worker_label,
                "date": date_s,
                "title": title,
                "start_time": start_s,
                "end_time": end_s,
                "location": location,
                "members": format_member_display_names(ev),
                "start_at": start_at.astimezone(TZ),
            }
        )
    return rows


def collect_worker_calendar_rows_for_week(
    *,
    week_start: date,
    workers: List[Dict[str, Any]],
    session_tokens: Optional[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """有効な職人のカレンダーから週の予定行を収集する."""
    week_start = sunday_week_containing(week_start)
    time_min, time_max = _fetch_time_range_for_week_offsets(week_start, list(range(7)))

    prefetch_pairs: List[Tuple[Credentials, str]] = []
    seen_cal: Set[str] = set()
    cal_id_to_meta: Dict[str, Tuple[str, Optional[str]]] = {}

    for w in workers:
        if not w.get("is_active", True):
            continue
        cal_id = str(w.get("calendar_id") or "").strip()
        if not cal_id:
            continue
        creds = _worker_credentials(w, session_tokens)
        if not creds:
            continue
        label = str(w.get("name") or w.get("worker_id") or cal_id)
        cal_id_to_meta[cal_id] = (label, _calendar_owner_email(w))
        if cal_id not in seen_cal:
            seen_cal.add(cal_id)
            prefetch_pairs.append((creds, cal_id))

    warnings: List[str] = []
    out: List[Dict[str, Any]] = []
    if not prefetch_pairs:
        warnings.append("取得可能な職人カレンダーがありません（calendar_id または OAuth 連携を確認してください）。")
        return out, warnings

    bundle, fetch_errors = _parallel_fetch_events_by_calendar_id_with_errors(
        prefetch_pairs, time_min, time_max
    )
    for cal_id, err in (fetch_errors or {}).items():
        meta = cal_id_to_meta.get(cal_id)
        if meta and err:
            warnings.append(f"職人:{meta[0]} の予定取得に失敗しました: {err}")

    for cal_id, events in bundle.items():
        meta = cal_id_to_meta.get(cal_id)
        if not meta:
            continue
        label, owner_email = meta
        out.extend(
            _events_to_collect_rows(
                events,
                week_start=week_start,
                worker_label=label,
                calendar_id=cal_id,
                owner_email=owner_email,
            )
        )

    out.sort(key=lambda r: (r["start_at"], r["worker_label"], r["title"]))
    return out, warnings
