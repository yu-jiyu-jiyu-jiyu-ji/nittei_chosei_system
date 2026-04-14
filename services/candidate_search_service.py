"""候補検索（カレンダー・Maps・車両ルールを統合。OAuth 未整備時は候補なし）。"""

from __future__ import annotations

import concurrent.futures
import os
from datetime import date, datetime, time, timedelta, timezone
from itertools import combinations
from typing import Any, Dict, List, Optional, Set, Tuple
from zoneinfo import ZoneInfo

from google.oauth2.credentials import Credentials

from services.calendar_service import (
    count_completed_events_before_cached,
    event_location,
    event_time_bounds,
    get_next_event_after_cached,
    get_previous_event_before,
    get_previous_event_before_cached,
    interval_free_cached,
    list_events_in_range,
    list_events_in_range_safe,
)
from services.google_oauth_service import (
    credentials_from_refresh_token,
    load_vehicle_token_json_path,
)
from services.maps_service import (
    maps_api_key_configured,
    travel_duration_minutes,
    travel_duration_minutes_prefetch,
)
from services.vehicle_assignment_service import assign_vehicles_for_crew

TZ = ZoneInfo("Asia/Tokyo")


def _parse_positive_int(value: Any, default: int) -> int:
    """正の整数を安全にパース。不正時は default。"""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return n if n > 0 else default


def _n_choose_k_exceeds(n: int, k: int, threshold: int) -> bool:
    """nCk が threshold を超えるかを途中打ち切りで判定する。"""
    if threshold <= 0:
        return True
    if k < 0 or n < 0 or k > n:
        return False
    k = min(k, n - k)
    if k == 0:
        return 1 > threshold
    result = 1
    for i in range(1, k + 1):
        result = (result * (n - k + i)) // i
        if result > threshold:
            return True
    return False


def _bounded_worker_pool_size(total_workers: int, headcount: int, max_combinations: int) -> int:
    """組み合わせ爆発を避けるため、探索対象にする職人母集団サイズを上限化する。"""
    if total_workers <= headcount:
        return total_workers
    if max_combinations <= 0:
        return total_workers
    size = total_workers
    while size > headcount and _n_choose_k_exceeds(size, headcount, max_combinations):
        size -= 1
    return max(headcount, size)


def _sunday_week_start(d: date) -> date:
    """d を含む週の日曜日（日〜土の週の開始）を返す。Python weekday: 月=0 … 日=6."""
    return d - timedelta(days=(d.weekday() + 1) % 7)


def sunday_week_containing(d: date) -> date:
    """d を含む週の日曜日（UI・検索の週の起点）。"""
    return _sunday_week_start(d)


def _parse_hhmm(s: str, default_h: int, default_m: int) -> Tuple[int, int]:
    """HH:MM 形式を (時, 分) に。不正時はデフォルト。"""
    raw = (s or "").strip()
    parts = raw.split(":")
    if len(parts) != 2:
        return default_h, default_m
    try:
        h = int(parts[0])
        m = int(parts[1])
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h, m
    except ValueError:
        pass
    return default_h, default_m


def work_hours_display_hours(settings: Dict[str, Any]) -> Tuple[int, int]:
    """週カレンダー表示用の開始・終了「時」（分は切り捨てで時のみ使用）。"""
    ws = _parse_hhmm(str(settings.get("work_hours_start") or "07:00"), 7, 0)
    we = _parse_hhmm(str(settings.get("work_hours_end") or "19:00"), 19, 0)
    return ws[0], we[0]


def _worker_credentials(
    worker: Dict[str, Any],
    session_tokens: Optional[Dict[str, Any]],
) -> Optional[Credentials]:
    wid = worker.get("worker_id")
    if session_tokens and wid in session_tokens:
        rt = (session_tokens[wid] or {}).get("refresh_token")
        if rt:
            c = credentials_from_refresh_token(rt)
            if c:
                return c
    rt = worker.get("google_refresh_token")
    if rt:
        return credentials_from_refresh_token(str(rt))
    return None


def _vehicle_fleet_credentials(
    settings: Optional[Dict[str, Any]] = None,
    vehicle_fleet_session: Optional[Dict[str, Any]] = None,
) -> Optional[Credentials]:
    """全車共通のフォールバック。優先: セッション → Firestore 設定 → .env の順。"""
    if vehicle_fleet_session and vehicle_fleet_session.get("refresh_token"):
        c = credentials_from_refresh_token(str(vehicle_fleet_session["refresh_token"]))
        if c:
            return c
    if settings:
        rt = (settings.get("google_vehicle_refresh_token") or "").strip()
        if rt:
            c = credentials_from_refresh_token(rt)
            if c:
                return c
    path = os.environ.get("GOOGLE_VEHICLE_CALENDAR_TOKEN_JSON", "").strip()
    if path:
        c = load_vehicle_token_json_path(path)
        if c:
            return c
    rt = os.environ.get("GOOGLE_VEHICLE_CALENDAR_REFRESH_TOKEN", "").strip()
    if rt:
        return credentials_from_refresh_token(rt)
    return None


def _vehicle_calendar_credentials(
    vehicle: Dict[str, Any],
    session_tokens: Optional[Dict[str, Any]],
    settings: Optional[Dict[str, Any]] = None,
    vehicle_fleet_session: Optional[Dict[str, Any]] = None,
) -> Optional[Credentials]:
    """車両1台分の Calendar API 用 Credentials。職人と同様に車両 ID 単位 → なければ共通フォールバック。"""
    vid = str(vehicle.get("vehicle_id", ""))
    if session_tokens and vid in session_tokens:
        rt = (session_tokens[vid] or {}).get("refresh_token")
        if rt:
            c = credentials_from_refresh_token(str(rt))
            if c:
                return c
    rt = vehicle.get("google_refresh_token")
    if rt:
        c = credentials_from_refresh_token(str(rt))
        if c:
            return c
    return _vehicle_fleet_credentials(settings, vehicle_fleet_session)


def _location_override_key(worker_id: str, event_id: str) -> str:
    return f"{worker_id}:{event_id}"


def _required_headcount(
    project: Optional[Dict[str, Any]],
    ui_capacity: int,
) -> int:
    if project:
        try:
            rw = int(project.get("required_workers") or 0)
        except (TypeError, ValueError):
            rw = 0
        return max(rw, int(ui_capacity))
    return max(0, int(ui_capacity))


def _work_minutes(project: Optional[Dict[str, Any]]) -> int:
    if not project:
        return 120
    try:
        m = int(project.get("work_duration_minutes") or 120)
    except (TypeError, ValueError):
        m = 120
    return max(30, m)


def count_completed_events_before(
    creds: Credentials,
    calendar_id: str,
    day_start: datetime,
    before: datetime,
) -> int:
    """day_start 〜 before の間に終了した予定の件数（現場1件＝予定1件）."""
    evs = list_events_in_range(creds, calendar_id, day_start, before)
    n = 0
    for ev in evs:
        b = event_time_bounds(ev)
        if not b:
            continue
        _, ee = b
        if ee <= before:
            n += 1
    return n


def material_return_extra_minutes(
    creds: Credentials,
    calendar_id: str,
    day_start: datetime,
    slot_start: datetime,
    office_address: str,
    next_site_address: str,
    load_minutes: int,
) -> float:
    """資材2件消化後（同日に終了済み件数が2,4,6…）の拠点戻り＋積込＋拠点→次現場の分数。"""
    n = count_completed_events_before(creds, calendar_id, day_start, slot_start)
    if n < 2 or (n % 2) != 0:
        return 0.0
    if not maps_api_key_configured() or not office_address.strip() or not next_site_address.strip():
        return 0.0
    prev = get_previous_event_before(creds, calendar_id, slot_start, day_start=day_start)
    origin = event_location(prev) if prev else ""
    if not origin.strip():
        origin = office_address
    to_office = travel_duration_minutes(origin, office_address) or 0.0
    to_site = travel_duration_minutes(office_address, next_site_address) or 0.0
    return float(load_minutes) + to_office + to_site


def _parallel_fetch_events_by_calendar_id(
    prefetch_pairs: List[Tuple[Credentials, str]],
    time_min_fetch: datetime,
    time_max_fetch: datetime,
) -> Dict[str, List[Dict[str, Any]]]:
    """calendar_id ごとに週の予定を取得（同一 ID は上書き）."""
    out: Dict[str, List[Dict[str, Any]]] = {}
    if not prefetch_pairs:
        return out
    if len(prefetch_pairs) <= 1:
        for c, cid in prefetch_pairs:
            try:
                out[cid] = list_events_in_range(c, cid, time_min_fetch, time_max_fetch)
            except Exception:
                out[cid] = []
        return out
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(prefetch_pairs))) as ex:
        fut_to_cid: Dict[Any, str] = {}
        for c, cid in prefetch_pairs:
            fut = ex.submit(list_events_in_range, c, cid, time_min_fetch, time_max_fetch)
            fut_to_cid[fut] = cid
        for fut in concurrent.futures.as_completed(fut_to_cid):
            cid = fut_to_cid[fut]
            try:
                out[cid] = fut.result()
            except Exception:
                out[cid] = []
    return out


def _parallel_fetch_events_by_calendar_id_with_errors(
    prefetch_pairs: List[Tuple[Credentials, str]],
    time_min_fetch: datetime,
    time_max_fetch: datetime,
) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, str]]:
    """calendar_id ごとの予定取得と失敗理由を返す。"""
    out: Dict[str, List[Dict[str, Any]]] = {}
    errors: Dict[str, str] = {}
    if not prefetch_pairs:
        return out, errors
    if len(prefetch_pairs) <= 1:
        for c, cid in prefetch_pairs:
            events, err = list_events_in_range_safe(c, cid, time_min_fetch, time_max_fetch)
            out[cid] = events
            if err:
                errors[cid] = err
        return out, errors
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(prefetch_pairs))) as ex:
        fut_to_cid: Dict[Any, str] = {}
        for c, cid in prefetch_pairs:
            fut = ex.submit(list_events_in_range_safe, c, cid, time_min_fetch, time_max_fetch)
            fut_to_cid[fut] = cid
        for fut in concurrent.futures.as_completed(fut_to_cid):
            cid = fut_to_cid[fut]
            try:
                events, err = fut.result()
                out[cid] = events
                if err:
                    errors[cid] = err
            except Exception as e:
                out[cid] = []
                errors[cid] = str(e)
    return out, errors


def material_return_extra_minutes_cached(
    events: List[Dict[str, Any]],
    day_start: datetime,
    slot_start: datetime,
    office_address: str,
    next_site_address: str,
    load_minutes: int,
) -> float:
    """material_return_extra_minutes のキャッシュ版（週一括取得した events を利用）."""
    n = count_completed_events_before_cached(events, day_start, slot_start)
    if n < 2 or (n % 2) != 0:
        return 0.0
    if not maps_api_key_configured() or not office_address.strip() or not next_site_address.strip():
        return 0.0
    prev = get_previous_event_before_cached(events, slot_start, day_start=day_start)
    origin = event_location(prev) if prev else ""
    if not origin.strip():
        origin = office_address
    to_office = travel_duration_minutes(origin, office_address) or 0.0
    to_site = travel_duration_minutes(office_address, next_site_address) or 0.0
    return float(load_minutes) + to_office + to_site


def fetch_week_calendar_events_bundle(
    *,
    project: Optional[Dict[str, Any]],
    workers: List[Dict[str, Any]],
    vehicles: List[Dict[str, Any]],
    settings: Dict[str, Any],
    ui_capacity: int,
    session_tokens: Optional[Dict[str, Any]] = None,
    vehicle_fleet_session: Optional[Dict[str, Any]] = None,
    excluded_worker_ids: Optional[Set[str]] = None,
    search_week_start: Optional[date] = None,
) -> Tuple[Optional[Dict[str, List[Dict[str, Any]]]], List[str]]:
    """候補検索の前段として、週の Google カレンダー予定だけを取得する（API はこの1回分）.

    検証に通らない場合は (None, warnings)。成功時は (calendar_id -> events, warnings)。
    """
    warnings: List[str] = []
    headcount = _required_headcount(project, ui_capacity)
    if headcount <= 0:
        return None, ["必要人数が 0 です。案件を選ぶか人数を指定してください。"]

    v_assign = assign_vehicles_for_crew(headcount, vehicles)
    if v_assign is None:
        warnings.append(
            "車両の割当ができません。利用中かつ利用可能な車両のうち、"
            "少なくとも 2人乗りまたは 3人乗りが 1 台以上必要です（無効化のみ・定員4のみでは割当できない場合があります）。"
            "車両マスタで有効な車を戻すか、車両を追加してください。"
        )
        return None, warnings

    active_workers = [w for w in workers if w.get("is_active", True)]
    creds_map: Dict[str, Credentials] = {}
    for w in active_workers:
        c = _worker_credentials(w, session_tokens)
        if c:
            creds_map[str(w["worker_id"])] = c

    fleet = _vehicle_fleet_credentials(settings, vehicle_fleet_session)
    active_vehicles = [v for v in vehicles if v.get("is_active", True)]
    vehicles_with_cal = [v for v in active_vehicles if str(v.get("calendar_id") or "").strip()]
    vehicles_missing_creds: List[str] = []
    if vehicles_with_cal:
        for v in vehicles_with_cal:
            if _vehicle_calendar_credentials(v, session_tokens, settings, vehicle_fleet_session) is None:
                vehicles_missing_creds.append(str(v.get("vehicle_id", "?")))
        vehicle_access_ok = len(vehicles_missing_creds) == 0
    else:
        vehicle_access_ok = fleet is not None

    use_real = len(creds_map) >= headcount and vehicle_access_ok
    if not use_real:
        if len(creds_map) < headcount:
            warnings.append(
                "職人の Google カレンダー連携（または保存トークン）が不足しているため、候補を出せません。"
                "共通設定で必要人数分の OAuth 連携を完了してください。"
            )
        if not vehicle_access_ok:
            detail = (
                f"（カレンダー認証が不足している車両: {', '.join(vehicles_missing_creds)}）"
                if vehicles_missing_creds
                else ""
            )
            warnings.append(
                "車両の Google カレンダーが参照できません。"
                "有効な全車両で OAuth 連携が済むか、または 1 つの Google アカウントで全カレンダーを読む場合は共通設定の「車両共通フォールバック」を設定してください。"
                "（1台だけ連携済でも、他車にカレンダー ID だけ入っているとこのままでは警告になります。）"
                + detail
            )
        return None, warnings

    excl = excluded_worker_ids or set()
    active_ids = {str(w["worker_id"]) for w in active_workers}
    ready_ids = [wid for wid in creds_map if wid in active_ids and wid not in excl]
    if len(ready_ids) < headcount:
        warnings.append(
            "条件に合う連携済み職人が不足しています（除外・含む条件・必要人数を確認してください）。"
        )
        return None, warnings

    wid_to_worker = {str(w["worker_id"]): w for w in active_workers}
    today = datetime.now(TZ).date()
    week_start = (
        _sunday_week_start(search_week_start) if search_week_start is not None else _sunday_week_start(today)
    )
    time_min_fetch = datetime.combine(week_start, time.min, tzinfo=TZ) - timedelta(days=1)
    time_max_fetch = datetime.combine(week_start + timedelta(days=8), time.min, tzinfo=TZ)

    prefetch_pairs: List[Tuple[Credentials, str]] = []
    seen_cal: Set[str] = set()
    for wid in ready_ids:
        w = wid_to_worker[wid]
        cid = str(w.get("calendar_id") or "").strip()
        if not cid:
            continue
        c = creds_map[wid]
        if cid not in seen_cal:
            seen_cal.add(cid)
            prefetch_pairs.append((c, cid))
    for v in vehicles:
        if not v.get("is_active", True):
            continue
        cid = str(v.get("calendar_id") or "").strip()
        if not cid:
            continue
        vc = _vehicle_calendar_credentials(v, session_tokens, settings, vehicle_fleet_session)
        if vc:
            if cid not in seen_cal:
                seen_cal.add(cid)
                prefetch_pairs.append((vc, cid))

    bundle, fetch_errors = _parallel_fetch_events_by_calendar_id_with_errors(
        prefetch_pairs, time_min_fetch, time_max_fetch
    )
    if fetch_errors:
        for v in active_vehicles:
            vid = str(v.get("vehicle_id") or "?")
            cid = str(v.get("calendar_id") or "").strip()
            if not cid:
                continue
            err = fetch_errors.get(cid)
            if err:
                warnings.append(f"車両 {vid} の予定取得に失敗しました: {err}")
    return bundle, warnings


def search_candidates(
    *,
    project: Optional[Dict[str, Any]],
    workers: List[Dict[str, Any]],
    vehicles: List[Dict[str, Any]],
    settings: Dict[str, Any],
    ui_capacity: int,
    session_tokens: Optional[Dict[str, Any]] = None,
    vehicle_fleet_session: Optional[Dict[str, Any]] = None,
    location_overrides: Optional[Dict[str, str]] = None,
    excluded_worker_ids: Optional[Set[str]] = None,
    must_include_worker_ids: Optional[List[str]] = None,
    search_week_start: Optional[date] = None,
    limit_search_days: Optional[List[date]] = None,
    shared_events_by_calendar_id: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """候補一覧と警告メッセージ群を返す.

    search_week_start: 検索対象週の「含まれる任意の日」。None のときは今週（当日を含む日曜始まり）。
    limit_search_days: 指定時はその日だけを走査（UI の分割検索・タイムアウト対策用）。
    shared_events_by_calendar_id: 週の予定を呼び出し元で取得済みのとき渡す（Google カレンダー API を再実行しない）。
    """
    warnings: List[str] = []
    loc_ov = location_overrides or {}
    warned_pool_bounded = False
    warned_combo_truncated = False
    warned_timeout = False

    headcount = _required_headcount(project, ui_capacity)
    if headcount <= 0:
        return [], ["必要人数が 0 です。案件を選ぶか人数を指定してください。"]

    v_assign = assign_vehicles_for_crew(headcount, vehicles)
    if v_assign is None:
        warnings.append(
            "車両の割当ができません。利用中かつ利用可能な車両のうち、"
            "少なくとも 2人乗りまたは 3人乗りが 1 台以上必要です（無効化のみ・定員4のみでは割当できない場合があります）。"
            "車両マスタで有効な車を戻すか、車両を追加してください。"
        )
        return [], warnings

    active_workers = [w for w in workers if w.get("is_active", True)]
    creds_map: Dict[str, Credentials] = {}
    for w in active_workers:
        c = _worker_credentials(w, session_tokens)
        if c:
            creds_map[str(w["worker_id"])] = c

    fleet = _vehicle_fleet_credentials(settings, vehicle_fleet_session)
    active_vehicles = [v for v in vehicles if v.get("is_active", True)]
    vehicles_with_cal = [v for v in active_vehicles if str(v.get("calendar_id") or "").strip()]
    vehicles_missing_creds: List[str] = []
    if vehicles_with_cal:
        for v in vehicles_with_cal:
            if _vehicle_calendar_credentials(v, session_tokens, settings, vehicle_fleet_session) is None:
                vehicles_missing_creds.append(str(v.get("vehicle_id", "?")))
        vehicle_access_ok = len(vehicles_missing_creds) == 0
    else:
        vehicle_access_ok = fleet is not None

    use_real = len(creds_map) >= headcount and vehicle_access_ok

    if not use_real:
        if len(creds_map) < headcount:
            warnings.append(
                "職人の Google カレンダー連携（または保存トークン）が不足しているため、候補を出せません。"
                "共通設定で必要人数分の OAuth 連携を完了してください。"
            )
        if not vehicle_access_ok:
            detail = (
                f"（カレンダー認証が不足している車両: {', '.join(vehicles_missing_creds)}）"
                if vehicles_missing_creds
                else ""
            )
            warnings.append(
                "車両の Google カレンダーが参照できません。"
                "有効な全車両で OAuth 連携が済むか、または 1 つの Google アカウントで全カレンダーを読む場合は共通設定の「車両共通フォールバック」を設定してください。"
                "（1台だけ連携済でも、他車にカレンダー ID だけ入っているとこのままでは警告になります。）"
                + detail
            )
        return [], warnings

    duration_min = _work_minutes(project)
    try:
        slot_min = int(settings.get("time_slot_minutes") or 30)
    except (TypeError, ValueError):
        slot_min = 30
    office = str(settings.get("office_address") or "").strip()
    try:
        load_min = int(settings.get("load_minutes") or 20)
    except (TypeError, ValueError):
        load_min = 20
    max_candidate_count = _parse_positive_int(settings.get("max_candidate_count"), 20)
    max_combinations_per_slot = _parse_positive_int(
        settings.get("max_combinations_per_slot"), 300
    )
    search_time_limit_seconds = _parse_positive_int(
        settings.get("search_time_limit_seconds"), 20
    )
    search_started_at = datetime.now(TZ)

    project_address = str(project.get("address", "") if project else "").strip()
    if not project_address:
        warnings.append("案件住所が空のため、移動時間判定をスキップします。")

    wh_s = _parse_hhmm(str(settings.get("work_hours_start") or "07:00"), 7, 0)
    wh_e = _parse_hhmm(str(settings.get("work_hours_end") or "19:00"), 19, 0)
    work_start_min = wh_s[0] * 60 + wh_s[1]
    work_end_min = wh_e[0] * 60 + wh_e[1]
    work_hours_filter = work_start_min < work_end_min
    if not work_hours_filter:
        warnings.append(
            "就業時間の開始が終了以上です。共通設定の「就業時間」を確認してください。"
        )

    excl = excluded_worker_ids or set()
    active_ids = {str(w["worker_id"]) for w in active_workers}
    ready_ids = [wid for wid in creds_map if wid in active_ids and wid not in excl]
    if len(ready_ids) < headcount:
        warnings.append(
            "条件に合う連携済み職人が不足しています（除外・含む条件・必要人数を確認してください）。"
        )
        return [], warnings

    candidates: List[Dict[str, Any]] = []
    wid_to_worker = {str(w["worker_id"]): w for w in active_workers}
    vehicle_by_id = {str(v["vehicle_id"]): v for v in vehicles}

    today = datetime.now(TZ).date()
    maps_ok = maps_api_key_configured()
    # 検索は日曜始まりの7日間（search_week_start で対象週を指定可能）
    week_start = (
        _sunday_week_start(search_week_start) if search_week_start is not None else _sunday_week_start(today)
    )
    search_days = [week_start + timedelta(days=i) for i in range(7)]
    if limit_search_days is not None:
        lim = set(limit_search_days)
        search_days = [d for d in search_days if d in lim]
        if not search_days:
            warnings.append("指定された検索日が対象週に含まれません。")
            return [], warnings

    # 各カレンダーは週（＋端の余白）で1回だけ取得し、スロットごとの API 再取得を避ける
    time_min_fetch = datetime.combine(week_start, time.min, tzinfo=TZ) - timedelta(days=1)
    time_max_fetch = datetime.combine(week_start + timedelta(days=8), time.min, tzinfo=TZ)
    # calendar_id 文字列をキーにする（分割検索でセッション共有するため id(creds) は使わない）
    events_by_cal_id: Dict[str, List[Dict[str, Any]]] = {}

    def _week_events(creds: Credentials, cal_id: str) -> List[Dict[str, Any]]:
        return list(events_by_cal_id.get(cal_id) or [])

    # 週の予定を職人・車両カレンダーごとに1回だけ取得（直列だと API 往復が積み上がるため並列化）
    prefetch_pairs: List[Tuple[Credentials, str]] = []
    seen_cal: Set[str] = set()
    for wid in ready_ids:
        w = wid_to_worker[wid]
        cid = str(w.get("calendar_id") or "").strip()
        if not cid:
            continue
        c = creds_map[wid]
        if cid not in seen_cal:
            seen_cal.add(cid)
            prefetch_pairs.append((c, cid))
    for v in vehicles:
        if not v.get("is_active", True):
            continue
        cid = str(v.get("calendar_id") or "").strip()
        if not cid:
            continue
        vc = _vehicle_calendar_credentials(v, session_tokens, settings, vehicle_fleet_session)
        if vc:
            if cid not in seen_cal:
                seen_cal.add(cid)
                prefetch_pairs.append((vc, cid))

    vehicle_fetch_errors: Dict[str, str] = {}
    if shared_events_by_calendar_id is not None:
        events_by_cal_id = {k: list(v) for k, v in shared_events_by_calendar_id.items()}
    else:
        events_by_cal_id, vehicle_fetch_errors = _parallel_fetch_events_by_calendar_id_with_errors(
            prefetch_pairs, time_min_fetch, time_max_fetch
        )
    if vehicle_fetch_errors:
        for v in active_vehicles:
            vid = str(v.get("vehicle_id") or "?")
            cid = str(v.get("calendar_id") or "").strip()
            if not cid:
                continue
            err = vehicle_fetch_errors.get(cid)
            if err:
                warnings.append(f"車両 {vid} の予定取得に失敗しました: {err}")

    # 「含む」職人（スロットごとに組み合わせを絞るときに利用）
    anchor_ids: Set[str] = {str(x) for x in (must_include_worker_ids or []) if x}

    for d in search_days:
        day_start = datetime.combine(d, time.min, tzinfo=TZ)
        day_end = day_start + timedelta(days=1)

        minutes = 0
        while minutes < 24 * 60:
            if len(candidates) >= max_candidate_count:
                break
            elapsed = (datetime.now(TZ) - search_started_at).total_seconds()
            if elapsed > search_time_limit_seconds:
                if not warned_timeout:
                    warnings.append(
                        "検索時間の上限に到達したため、候補を一部のみ返しました。"
                        "必要なら共通設定の探索条件を調整してください。"
                    )
                    warned_timeout = True
                break
            slot_start = day_start + timedelta(minutes=minutes)
            slot_end = slot_start + timedelta(minutes=duration_min)
            minutes += slot_min

            if slot_end <= datetime.now(TZ):
                continue

            if work_hours_filter:
                sm_rel = int((slot_start - day_start).total_seconds() // 60)
                em_rel = int((slot_end - day_start).total_seconds() // 60)
                if sm_rel < work_start_min or em_rel > work_end_min:
                    continue

            # カレンダー上その枠が空いている職人だけに絞ってから組み合わせる（C(n,k) の n を大幅削減）
            slot_free_ids: List[str] = []
            for wid in sorted(ready_ids):
                w = wid_to_worker[wid]
                cal_id = str(w.get("calendar_id") or "").strip()
                if not cal_id:
                    continue
                wc = creds_map[wid]
                ev_w = _week_events(wc, cal_id)
                if interval_free_cached(ev_w, slot_start, slot_end):
                    slot_free_ids.append(wid)

            if len(slot_free_ids) < headcount:
                continue
            if anchor_ids and not anchor_ids.issubset(set(slot_free_ids)):
                continue

            assigned_vids_slot = assign_vehicles_for_crew(headcount, vehicles)
            if not assigned_vids_slot:
                continue

            # B: Distance Matrix をスロット単位でまとめて取得（同一 OD はキャッシュ）
            if project_address and maps_ok:
                dm_pairs: List[Tuple[str, str]] = []
                pa = project_address.strip()
                for wid in slot_free_ids:
                    w = wid_to_worker[wid]
                    cal_id = str(w.get("calendar_id") or "").strip()
                    if not cal_id:
                        continue
                    wc = creds_map[wid]
                    ev_w = _week_events(wc, cal_id)
                    prev = get_previous_event_before_cached(ev_w, slot_start, day_start=day_start)
                    if prev:
                        loc = event_location(prev)
                        pid = prev.get("id") or ""
                        okey = _location_override_key(wid, str(pid))
                        if not loc and okey in loc_ov:
                            loc = loc_ov[okey]
                        if loc:
                            dm_pairs.append((loc.strip(), pa))
                    nxt = get_next_event_after_cached(
                        ev_w, slot_end, day_start=day_start, day_end=day_end
                    )
                    if nxt:
                        loc_n = event_location(nxt)
                        nid = nxt.get("id") or ""
                        okey_n = _location_override_key(wid, str(nid))
                        if not loc_n and okey_n in loc_ov:
                            loc_n = loc_ov[okey_n]
                        if loc_n:
                            dm_pairs.append((pa, loc_n.strip()))
                if office:
                    oa = office.strip()
                    for vid in assigned_vids_slot:
                        v = vehicle_by_id.get(str(vid))
                        if not v:
                            continue
                        vcreds = _vehicle_calendar_credentials(
                            v, session_tokens, settings, vehicle_fleet_session
                        )
                        vcal = str(v.get("calendar_id") or "").strip()
                        if not vcreds or not vcal:
                            continue
                        ev_v = _week_events(vcreds, vcal)
                        prev_v = get_previous_event_before_cached(ev_v, slot_start, day_start=day_start)
                        origin = event_location(prev_v) if prev_v else ""
                        if not origin.strip():
                            origin = office
                        if origin.strip() and oa:
                            dm_pairs.append((origin.strip(), oa))
                        if oa and pa:
                            dm_pairs.append((oa, pa))
                travel_duration_minutes_prefetch(dm_pairs)

            # C: 移動・住所だけで不可能な職人を組み合わせ前に除外（内側ループと同じ判定）
            slot_pruned: List[str] = []
            for wid in slot_free_ids:
                w = wid_to_worker[wid]
                cal_id = str(w.get("calendar_id") or "").strip()
                if not cal_id:
                    continue
                wc = creds_map[wid]
                ev_w = _week_events(wc, cal_id)
                ok_w = True
                prev = get_previous_event_before_cached(ev_w, slot_start, day_start=day_start)
                if prev:
                    loc = event_location(prev)
                    pid = prev.get("id") or ""
                    okey = _location_override_key(wid, str(pid))
                    if not loc and okey in loc_ov:
                        loc = loc_ov[okey]
                    if not loc:
                        ok_w = False
                    elif project_address and maps_ok:
                        b = event_time_bounds(prev)
                        pe = b[1] if b else None
                        if pe:
                            tr = travel_duration_minutes(loc, project_address)
                            if tr is not None and pe + timedelta(minutes=tr) > slot_start:
                                ok_w = False
                if ok_w and project_address and maps_ok:
                    nxt = get_next_event_after_cached(
                        ev_w, slot_end, day_start=day_start, day_end=day_end
                    )
                    if nxt:
                        loc_n = event_location(nxt)
                        nid = nxt.get("id") or ""
                        okey_n = _location_override_key(wid, str(nid))
                        if not loc_n and okey_n in loc_ov:
                            loc_n = loc_ov[okey_n]
                        if not loc_n:
                            ok_w = False
                        else:
                            nb = event_time_bounds(nxt)
                            ns = nb[0] if nb else None
                            if ns:
                                tr_n = travel_duration_minutes(
                                    project_address.strip(), loc_n.strip()
                                )
                                if tr_n is not None and slot_end + timedelta(minutes=tr_n) > ns:
                                    ok_w = False
                if ok_w:
                    slot_pruned.append(wid)
            slot_free_ids = slot_pruned

            if len(slot_free_ids) < headcount:
                continue
            if anchor_ids and not anchor_ids.issubset(set(slot_free_ids)):
                continue

            pool_ids = sorted(slot_free_ids)
            bounded_size = _bounded_worker_pool_size(
                len(pool_ids), headcount, max_combinations_per_slot
            )
            if bounded_size < len(pool_ids):
                pool_ids = pool_ids[:bounded_size]
                if not warned_pool_bounded:
                    warnings.append(
                        "組み合わせ数が多いため、探索対象の職人候補を絞って高速化しています。"
                    )
                    warned_pool_bounded = True
            if len(pool_ids) < headcount:
                continue

            combo_iter = combinations(pool_ids, headcount)
            combos_checked = 0
            for combo in combo_iter:
                if combos_checked >= max_combinations_per_slot:
                    if not warned_combo_truncated:
                        warnings.append(
                            "1枠あたりの探索上限に達したため、一部の組み合わせを省略しました。"
                        )
                        warned_combo_truncated = True
                    break
                combos_checked += 1
                if anchor_ids and not anchor_ids.issubset(combo):
                    continue
                ok = True
                worker_ids = list(combo)
                assigned_vids = assigned_vids_slot
                # 同一スロットで「前現場→現場」の移動時間を2回計算しない（検証ループの結果を表示用に再利用）
                prev_to_site_minutes: Dict[str, Optional[float]] = {}

                for wid in worker_ids:
                    w = wid_to_worker[wid]
                    cal_id = str(w.get("calendar_id") or "").strip()
                    if not cal_id:
                        ok = False
                        break
                    wc = creds_map[wid]
                    ev_w = _week_events(wc, cal_id)
                    if not interval_free_cached(ev_w, slot_start, slot_end):
                        ok = False
                        break

                    prev = get_previous_event_before_cached(ev_w, slot_start, day_start=day_start)
                    if prev:
                        pid = prev.get("id") or ""
                        loc = event_location(prev)
                        okey = _location_override_key(wid, str(pid))
                        if not loc and okey in loc_ov:
                            loc = loc_ov[okey]
                        if not loc:
                            ok = False
                            break
                        if project_address and maps_ok:
                            b = event_time_bounds(prev)
                            pe = b[1] if b else None
                            if pe:
                                tr = travel_duration_minutes(loc, project_address)
                                prev_to_site_minutes[wid] = tr
                                # None のときは API 失敗等のため移動制約をかけず、空き枠は採用する
                                if tr is not None and (
                                    pe + timedelta(minutes=tr) > slot_start
                                ):
                                    ok = False
                                    break

                    if ok and project_address and maps_ok:
                        nxt = get_next_event_after_cached(
                            ev_w, slot_end, day_start=day_start, day_end=day_end
                        )
                        if nxt:
                            nid = nxt.get("id") or ""
                            loc_n = event_location(nxt)
                            okey_n = _location_override_key(wid, str(nid))
                            if not loc_n and okey_n in loc_ov:
                                loc_n = loc_ov[okey_n]
                            if not loc_n:
                                ok = False
                                break
                            nb = event_time_bounds(nxt)
                            ns = nb[0] if nb else None
                            if ns:
                                tr_n = travel_duration_minutes(
                                    project_address.strip(), loc_n.strip()
                                )
                                if tr_n is not None and (
                                    slot_end + timedelta(minutes=tr_n) > ns
                                ):
                                    ok = False
                                    break

                if not ok:
                    continue

                material_extra_first: Optional[float] = None
                first_vid = str(assigned_vids[0]) if assigned_vids else None

                for vid in assigned_vids:
                    v = vehicle_by_id.get(vid)
                    if not v:
                        ok = False
                        break
                    vcal = str(v.get("calendar_id") or "").strip()
                    if not vcal:
                        ok = False
                        break
                    vcreds = _vehicle_calendar_credentials(
                        v, session_tokens, settings, vehicle_fleet_session
                    )
                    if not vcreds:
                        ok = False
                        break
                    ev_v = _week_events(vcreds, vcal)
                    if not interval_free_cached(ev_v, slot_start, slot_end):
                        ok = False
                        break
                    if project_address and maps_ok and office:
                        extra = material_return_extra_minutes_cached(
                            ev_v,
                            day_start,
                            slot_start,
                            office,
                            project_address,
                            load_min,
                        )
                        if first_vid is not None and str(vid) == first_vid:
                            material_extra_first = float(extra)
                        if extra > 0:
                            prev_v = get_previous_event_before_cached(
                                ev_v, slot_start, day_start=day_start
                            )
                            if prev_v:
                                vb = event_time_bounds(prev_v)
                                if vb:
                                    _, vee = vb
                                    if vee + timedelta(minutes=extra) > slot_start:
                                        ok = False
                                        break
                if not ok:
                    continue

                travel_by_worker: Dict[str, float] = {}
                travel_max: Optional[float] = None
                for wid in worker_ids:
                    w = wid_to_worker[wid]
                    cal_id_w = str(w.get("calendar_id") or "").strip()
                    wc = creds_map[wid]
                    ev_w = _week_events(wc, cal_id_w)
                    prev_tw = get_previous_event_before_cached(ev_w, slot_start, day_start=day_start)
                    if wid in prev_to_site_minutes:
                        tr_m = prev_to_site_minutes[wid]
                    elif prev_tw and project_address and maps_ok:
                        loc_tw = event_location(prev_tw)
                        pid_tw = prev_tw.get("id") or ""
                        okey_tw = _location_override_key(wid, str(pid_tw))
                        if not loc_tw and okey_tw in loc_ov:
                            loc_tw = loc_ov[okey_tw]
                        if loc_tw:
                            tr_m = travel_duration_minutes(loc_tw.strip(), project_address)
                        else:
                            tr_m = None
                    else:
                        tr_m = None
                    if tr_m is not None:
                        travel_by_worker[str(wid)] = round(float(tr_m), 1)
                        travel_max = (
                            float(tr_m)
                            if travel_max is None
                            else max(travel_max, float(tr_m))
                        )

                material_completed_count = 0
                material_extra_val = 0.0
                if assigned_vids:
                    v0 = vehicle_by_id.get(str(assigned_vids[0]))
                    if v0:
                        vcal0 = str(v0.get("calendar_id") or "").strip()
                        vc0 = _vehicle_calendar_credentials(
                            v0, session_tokens, settings, vehicle_fleet_session
                        )
                        if vc0 and vcal0:
                            ev_v0 = _week_events(vc0, vcal0)
                            material_completed_count = count_completed_events_before_cached(
                                ev_v0, day_start, slot_start
                            )
                            if material_extra_first is not None:
                                material_extra_val = material_extra_first
                            elif project_address and maps_ok and office:
                                material_extra_val = float(
                                    material_return_extra_minutes_cached(
                                        ev_v0,
                                        day_start,
                                        slot_start,
                                        office,
                                        project_address,
                                        load_min,
                                    )
                                )

                cid = f"R{d.isoformat().replace('-', '')}{slot_start.hour:02d}{slot_start.minute:02d}_{len(candidates)}"
                candidates.append(
                    {
                        "candidate_id": cid,
                        "start_at": slot_start.replace(tzinfo=None),
                        "end_at": slot_end.replace(tzinfo=None),
                        "capacity": headcount,
                        "worker_ids": worker_ids,
                        "vehicle_ids": assigned_vids,
                        "source": "calendar",
                        "travel_to_site_minutes_by_worker": travel_by_worker,
                        "travel_to_site_minutes_max": travel_max,
                        "material_completed_events_count": material_completed_count,
                        "material_extra_minutes": material_extra_val,
                    }
                )
                if len(candidates) >= max_candidate_count:
                    break
            if len(candidates) >= max_candidate_count:
                break
        if len(candidates) >= max_candidate_count:
            break

    if not candidates:
        if vehicle_fetch_errors:
            warnings.append(
                "候補ゼロの主因として、車両カレンダー参照不可（404 / invalid_grant 等）が疑われます。"
                "車両ごとの取得失敗メッセージを確認し、対象車両の OAuth 再連携またはカレンダー共有設定を見直してください。"
            )
        warnings.append(
            "条件を満たす実カレンダー候補がありませんでした。"
            "暫定住所の未入力や、連携・API キーを確認してください。"
        )
        return [], warnings

    if not maps_ok:
        warnings.append("GOOGLE_MAPS_API_KEY が未設定のため、移動・拠点戻り時間は一部スキップした可能性があります。")

    return candidates, warnings


def collect_week_busy_events(
    *,
    week_start: date,
    workers: List[Dict[str, Any]],
    vehicles: List[Dict[str, Any]],
    session_tokens: Optional[Dict[str, Any]],
    settings: Optional[Dict[str, Any]],
    vehicle_fleet_session: Optional[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """表示用：週（week_start 〜 土曜）の職人・車両カレンダーから予定を取得する.

    ブラウザで見ている Google アカウントと異なる場合、内容が一致しないことがある。
    """
    week_end = week_start + timedelta(days=6)
    time_min = datetime.combine(week_start, time.min, tzinfo=TZ)
    time_max = datetime.combine(week_end + timedelta(days=1), time.min, tzinfo=TZ)
    out: List[Dict[str, Any]] = []
    warnings: List[str] = []

    for w in workers:
        if not w.get("is_active", True):
            continue
        cal_id = str(w.get("calendar_id") or "").strip()
        if not cal_id:
            continue
        creds = _worker_credentials(w, session_tokens)
        if not creds:
            continue
        label = f"職人:{w.get('name', w.get('worker_id'))}"
        events, err = list_events_in_range_safe(creds, cal_id, time_min, time_max)
        if err:
            warnings.append(f"職人 {w.get('name', w.get('worker_id'))} の予定取得に失敗しました: {err}")
        for ev in events:
            b = event_time_bounds(ev)
            if not b:
                continue
            s, e = b
            out.append(
                {
                    "kind": "worker",
                    "label": label,
                    "calendar_id": cal_id,
                    "summary": (ev.get("summary") or "（無題）")[:120],
                    "start_at": s,
                    "end_at": e,
                }
            )

    for v in vehicles:
        if not v.get("is_active", True):
            continue
        cal_id = str(v.get("calendar_id") or "").strip()
        if not cal_id:
            continue
        creds = _vehicle_calendar_credentials(v, session_tokens, settings, vehicle_fleet_session)
        if not creds:
            continue
        label = f"車両:{v.get('name', v.get('vehicle_id'))}"
        events, err = list_events_in_range_safe(creds, cal_id, time_min, time_max)
        if err:
            warnings.append(f"車両 {v.get('name', v.get('vehicle_id'))} の予定取得に失敗しました: {err}")
        for ev in events:
            b = event_time_bounds(ev)
            if not b:
                continue
            s, e = b
            out.append(
                {
                    "kind": "vehicle",
                    "label": label,
                    "calendar_id": cal_id,
                    "summary": (ev.get("summary") or "（無題）")[:120],
                    "start_at": s,
                    "end_at": e,
                }
            )

    out.sort(key=lambda x: x["start_at"])
    return out, warnings


def format_week_events_jst_table_rows(events: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """collect_week_busy_events の結果を表表示用の文字列行にする."""
    rows: List[Dict[str, str]] = []
    for r in events:
        s = r["start_at"]
        e = r["end_at"]
        if s.tzinfo is None:
            s = s.replace(tzinfo=timezone.utc)
        if e.tzinfo is None:
            e = e.replace(tzinfo=timezone.utc)
        s = s.astimezone(TZ)
        e = e.astimezone(TZ)
        rows.append(
            {
                "開始": s.strftime("%m/%d %H:%M"),
                "終了": e.strftime("%m/%d %H:%M"),
                "種別": "職人" if r.get("kind") == "worker" else "車両",
                "表示名": str(r.get("label", "")),
                "参照カレンダーID": str(r.get("calendar_id", "")),
                "予定タイトル": str(r.get("summary", "")),
            }
        )
    return rows


def collect_missing_previous_locations(
    *,
    project: Optional[Dict[str, Any]],
    workers: List[Dict[str, Any]],
    ui_capacity: int,
    session_tokens: Optional[Dict[str, Any]],
    location_overrides: Optional[Dict[str, str]],
    search_date: date,
) -> List[Dict[str, Any]]:
    """前予定の住所欠落を一覧化。同一 event_id は先頭1件にまとめ可能。"""
    headcount = _required_headcount(project, ui_capacity)
    if headcount <= 0 or not project:
        return []

    active = [w for w in workers if w.get("is_active", True)]
    creds_map: Dict[str, Credentials] = {}
    for w in active:
        c = _worker_credentials(w, session_tokens)
        if c:
            creds_map[str(w["worker_id"])] = c

    if len(creds_map) < headcount:
        return []

    ready = [w for w in active if str(w["worker_id"]) in creds_map]
    day_start = datetime.combine(search_date, time.min, tzinfo=TZ)
    probe = day_start + timedelta(hours=12)

    missing: List[Dict[str, Any]] = []
    seen_event: Set[str] = set()
    loc_ov = location_overrides or {}

    for w in ready:
        wid = str(w["worker_id"])
        cal_id = str(w.get("calendar_id") or "").strip()
        if not cal_id:
            continue
        prev = get_previous_event_before(creds_map[wid], cal_id, probe, day_start=day_start)
        if not prev:
            continue
        eid = str(prev.get("id") or "")
        if not eid:
            continue
        if event_location(prev):
            continue
        key = _location_override_key(wid, eid)
        if key in loc_ov and loc_ov[key].strip():
            continue
        if eid not in seen_event:
            seen_event.add(eid)
            missing.append(
                {
                    "worker_id": wid,
                    "worker_name": w.get("name", wid),
                    "event_id": eid,
                    "event_summary": prev.get("summary") or "",
                    "override_key": key,
                }
            )

    return missing
