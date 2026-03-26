"""Google Calendar API（予定取得・空き判定・直前予定）."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from services.google_oauth_service import refresh_if_needed


def _service(creds: Credentials):
    c = refresh_if_needed(creds)
    return build("calendar", "v3", credentials=c, cache_discovery=False)


def _format_calendar_http_error(exc: HttpError) -> str:
    """Calendar API の HttpError をユーザー向けメッセージに整形（スコープ不足は対処を明示）。"""
    msg = ""
    try:
        if exc.content:
            data = json.loads(exc.content.decode("utf-8"))
            err = data.get("error") or {}
            if isinstance(err, dict):
                msg = str(err.get("message") or "").strip()
    except Exception:
        pass
    if not msg:
        msg = str(getattr(exc, "reason", None) or exc)
    low = msg.lower()
    status = getattr(getattr(exc, "resp", None), "status", None)
    if status == 404 or "not found" in low:
        return (
            "指定されたカレンダーが見つかりません（404）。"
            "IDの typo でない場合は、**車両・職人の「Google カレンダー連携」でログインしたアカウント**と、"
            "マスタの「GoogleカレンダーID」が**同じ Google アカウントのカレンダー**かを確認してください。"
            "別アカウントで連携していると、正しいメールアドレスでも API 上は存在しない扱いになります。"
            "連携アカウントの**主カレンダー**だけに書きたいときは、ID に **primary** と入れる方法もあります。"
            "（別カレンダーは設定の「カレンダーの統合」に表示される ID）"
            f"（API: {msg}）"
        )
    if "insufficient authentication scopes" in low:
        return (
            "認証スコープが不足しています（予定の新規登録には「書込み」権限が必要です）。"
            "Google Cloud コンソールの OAuth 同意画面に "
            "https://www.googleapis.com/auth/calendar.events を追加し、"
            "共通設定から該当の職人・車両で Google カレンダー連携をやり直してください。"
            "（以前に calendar.readonly のみで連携したトークンは、読取は可能でも登録はできません。）"
        )
    return f"Calendar API エラー: {msg}"


def list_events_in_range(
    creds: Credentials,
    calendar_id: str,
    time_min: datetime,
    time_max: datetime,
) -> List[Dict[str, Any]]:
    """指定期間の予定一覧（終日は除外しない。簡易）."""
    svc = _service(creds)
    tmin = time_min.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    tmax = time_max.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    events: List[Dict[str, Any]] = []
    page_token = None
    try:
        while True:
            resp = (
                svc.events()
                .list(
                    calendarId=calendar_id,
                    timeMin=tmin,
                    timeMax=tmax,
                    singleEvents=True,
                    orderBy="startTime",
                    pageToken=page_token,
                    maxResults=250,
                )
                .execute()
            )
            for item in resp.get("items", []):
                events.append(item)
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
    except HttpError:
        return []
    return events


def event_time_bounds(ev: Dict[str, Any]) -> Optional[Tuple[datetime, datetime]]:
    """予定の開始・終了（タイムゾーン付き）。"""
    return _event_bounds(ev)


def _event_bounds(ev: Dict[str, Any]) -> Optional[Tuple[datetime, datetime]]:
    start = ev.get("start", {})
    end = ev.get("end", {})
    if "dateTime" in start and "dateTime" in end:
        s = datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00"))
        e = datetime.fromisoformat(end["dateTime"].replace("Z", "+00:00"))
        return s, e
    if "date" in start and "date" in end:
        # 終日
        s = datetime.fromisoformat(start["date"]).replace(tzinfo=timezone.utc)
        e = datetime.fromisoformat(end["date"]).replace(tzinfo=timezone.utc)
        return s, e
    return None


def events_overlap_window(
    es: datetime,
    ee: datetime,
    window_start: datetime,
    window_end: datetime,
) -> bool:
    """[es, ee) と [window_start, window_end) が重なるか."""
    return es < window_end and ee > window_start


def interval_free_cached(
    events: List[Dict[str, Any]],
    interval_start: datetime,
    interval_end: datetime,
    *,
    buffer_before_minutes: float = 0,
    buffer_after_minutes: float = 0,
) -> bool:
    """list_events_in_range で取得済みの events を使い、API を呼ばず空き判定する."""
    if interval_start >= interval_end:
        return False
    pad_start = interval_start - timedelta(minutes=buffer_before_minutes)
    pad_end = interval_end + timedelta(minutes=buffer_after_minutes)
    for ev in events:
        b = event_time_bounds(ev)
        if not b:
            continue
        es, ee = b
        if es < pad_end and ee > pad_start:
            return False
    return True


def get_previous_event_before_cached(
    events: List[Dict[str, Any]],
    before: datetime,
    *,
    day_start: datetime,
) -> Optional[Dict[str, Any]]:
    """list_events_in_range(day_start, before) に相当する範囲のイベントから、終了が最大のものを返す."""
    best: Optional[Tuple[datetime, Dict[str, Any]]] = None
    for ev in events:
        b = event_time_bounds(ev)
        if not b:
            continue
        es, ee = b
        if not events_overlap_window(es, ee, day_start, before):
            continue
        if ee <= before:
            if best is None or ee > best[0]:
                best = (ee, ev)
    return best[1] if best else None


def get_next_event_after_cached(
    events: List[Dict[str, Any]],
    after: datetime,
    *,
    day_start: datetime,
    day_end: datetime,
) -> Optional[Dict[str, Any]]:
    """day_start〜day_end の範囲で、開始が after 以上で最も早い予定を返す（枠終了後の「次の予定」）."""
    best: Optional[Tuple[datetime, Dict[str, Any]]] = None
    for ev in events:
        b = event_time_bounds(ev)
        if not b:
            continue
        es, ee = b
        if not events_overlap_window(es, ee, day_start, day_end):
            continue
        if es >= after:
            if best is None or es < best[0]:
                best = (es, ev)
    return best[1] if best else None


def count_completed_events_before_cached(
    events: List[Dict[str, Any]],
    day_start: datetime,
    before: datetime,
) -> int:
    """day_start 〜 before の窓と重なり、かつ終了が before 以前の予定件数（list_events_in_range 相当）。"""
    n = 0
    for ev in events:
        b = event_time_bounds(ev)
        if not b:
            continue
        es, ee = b
        if not events_overlap_window(es, ee, day_start, before):
            continue
        if ee <= before:
            n += 1
    return n


def is_interval_free(
    creds: Credentials,
    calendar_id: str,
    interval_start: datetime,
    interval_end: datetime,
    *,
    buffer_before_minutes: float = 0,
    buffer_after_minutes: float = 0,
) -> bool:
    """[interval_start, interval_end) に既存予定と重なりがないか."""
    if interval_start >= interval_end:
        return False
    pad_start = interval_start - timedelta(minutes=buffer_before_minutes)
    pad_end = interval_end + timedelta(minutes=buffer_after_minutes)
    events = list_events_in_range(creds, calendar_id, pad_start - timedelta(days=1), pad_end + timedelta(days=1))
    for ev in events:
        b = event_time_bounds(ev)
        if not b:
            continue
        es, ee = b
        # 重なり
        if es < pad_end and ee > pad_start:
            return False
    return True


def get_previous_event_before(
    creds: Credentials,
    calendar_id: str,
    before: datetime,
    *,
    day_start: datetime,
) -> Optional[Dict[str, Any]]:
    """before より前で、day_start 以降に終了した直近の予定（現場1件＝予定1件の前提で利用）."""
    events = list_events_in_range(creds, calendar_id, day_start, before)
    best: Optional[Tuple[datetime, Dict[str, Any]]] = None
    for ev in events:
        b = event_time_bounds(ev)
        if not b:
            continue
        _, ee = b
        if ee <= before:
            if best is None or ee > best[0]:
                best = (ee, ev)
    return best[1] if best else None


def event_location(ev: Optional[Dict[str, Any]]) -> str:
    if not ev:
        return ""
    loc = ev.get("location") or ""
    return str(loc).strip()


def insert_calendar_event(
    creds: Credentials,
    calendar_id: str,
    summary: str,
    start: datetime,
    end: datetime,
    *,
    location: str = "",
    description: str = "",
    tz_name: str = "Asia/Tokyo",
) -> Tuple[bool, str]:
    """Google カレンダーに予定を1件追加する。戻り値は (成功, メッセージまたは event_id).

    calendar.events スコープが必要。失敗時は (False, 理由)。
    """
    if not calendar_id.strip():
        return False, "カレンダーIDが空です。"
    tz = ZoneInfo(tz_name)
    if start.tzinfo is None:
        start = start.replace(tzinfo=tz)
    else:
        start = start.astimezone(tz)
    if end.tzinfo is None:
        end = end.replace(tzinfo=tz)
    else:
        end = end.astimezone(tz)
    svc = _service(creds)
    body: Dict[str, Any] = {
        "summary": summary,
        "location": location or "",
        "description": description or "",
        "start": {
            "dateTime": start.isoformat(),
            "timeZone": tz_name,
        },
        "end": {
            "dateTime": end.isoformat(),
            "timeZone": tz_name,
        },
    }
    try:
        resp = (
            svc.events()
            .insert(calendarId=calendar_id.strip(), body=body)
            .execute()
        )
        eid = str(resp.get("id") or "")
        return True, eid
    except HttpError as e:
        return False, _format_calendar_http_error(e)
    except Exception as e:
        return False, str(e)


def delete_calendar_event(
    creds: Credentials,
    calendar_id: str,
    event_id: str,
) -> Tuple[bool, str]:
    """Google カレンダーの予定を1件削除する。404 は既に削除済みとみなして成功。"""
    if not calendar_id.strip() or not str(event_id).strip():
        return False, "カレンダーIDまたはイベントIDが空です。"
    svc = _service(creds)
    try:
        (
            svc.events()
            .delete(calendarId=calendar_id.strip(), eventId=str(event_id).strip())
            .execute()
        )
        return True, ""
    except HttpError as e:
        if getattr(getattr(e, "resp", None), "status", None) == 404:
            return True, ""
        return False, _format_calendar_http_error(e)
    except Exception as e:
        return False, str(e)
