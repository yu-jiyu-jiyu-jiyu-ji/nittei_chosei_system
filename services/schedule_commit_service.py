"""候補確定時に Google カレンダーへ予定を登録する（職人・車両）."""

from __future__ import annotations

import urllib.parse
from datetime import datetime, time, timedelta
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from services.calendar_service import (
    delete_calendar_event,
    event_location,
    event_time_bounds,
    get_next_event_after_cached,
    get_previous_event_before_cached,
    insert_calendar_event,
    list_events_in_range,
)
from services.candidate_search_service import (
    _vehicle_calendar_credentials,
    _worker_credentials,
)
from services.maps_service import maps_api_key_configured, travel_duration_minutes
from services.project_service import patch_project_fields

TZ = ZoneInfo("Asia/Tokyo")


def _google_maps_dir_url(origin: str, destination: str) -> str:
    o = (origin or "").strip()
    d = (destination or "").strip()
    if not o or not d:
        return ""
    return "https://www.google.com/maps/dir/?api=1&" + urllib.parse.urlencode(
        {"origin": o, "destination": d}
    )


def _travel_block_description(leg_label: str, from_addr: str, to_addr: str) -> str:
    lines = [leg_label, f"出発: {from_addr}", f"到着: {to_addr}"]
    url = _google_maps_dir_url(from_addr, to_addr)
    if url:
        lines.append(url)
    return "\n".join(lines)


def _dt_to_naive_local(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(TZ).replace(tzinfo=None)


def _append_ref(
    new_refs: List[Dict[str, Any]],
    *,
    kind: str,
    ref_id: str,
    calendar_id: str,
    event_id: str,
) -> None:
    new_refs.append(
        {
            "kind": kind,
            "ref_id": ref_id,
            "calendar_id": calendar_id,
            "event_id": event_id,
        }
    )


def _insert_work_and_travel_blocks(
    *,
    creds: Any,
    cal_id: str,
    kind: str,
    ref_id: str,
    label: str,
    start_at: datetime,
    end_at: datetime,
    work_summary: str,
    work_description: str,
    project_addr: str,
    messages: List[str],
    new_refs: List[Dict[str, Any]],
    candidate: Optional[Dict[str, Any]] = None,
    settings: Optional[Dict[str, Any]] = None,
) -> bool:
    """現場の前後に移動ブロックを挟んで登録する（職人・車両の両カレンダーで共通）.

    移動（前→現場）→現場→移動（現場→次）の順で挿入。現場が失敗した場合は直前に入れた移動（前）を削除する。

    車両カレンダーは予定が少なく前後イベントが取れないことが多いため、kind==vehicle では
    候補検索の travel_to_site_minutes_max と拠点住所で移動ブロックを補完する。
    """
    refs_before = len(new_refs)
    addr = (project_addr or "").strip()
    day_start_dt = datetime.combine(start_at.date(), time.min, tzinfo=TZ)
    day_end_dt = day_start_dt + timedelta(days=1)
    time_min_fetch = day_start_dt - timedelta(days=1)
    time_max_fetch = day_end_dt + timedelta(days=1)
    slot_start_for_cache = datetime.combine(start_at.date(), start_at.time(), tzinfo=TZ)
    slot_end_for_cache = datetime.combine(end_at.date(), end_at.time(), tzinfo=TZ)

    events = list_events_in_range(creds, cal_id, time_min_fetch, time_max_fetch)

    travel_before_eid: Optional[str] = None
    ok_all = True

    if addr and maps_api_key_configured():
        prev = get_previous_event_before_cached(
            events, slot_start_for_cache, day_start=day_start_dt
        )
        if prev:
            loc = event_location(prev)
            if loc.strip():
                b = event_time_bounds(prev)
                if b:
                    pe = b[1]
                    tr = travel_duration_minutes(loc.strip(), addr)
                    if tr is not None and tr > 0:
                        pe_n = _dt_to_naive_local(pe)
                        tr_end = pe_n + timedelta(minutes=float(tr))
                        if tr_end > start_at:
                            tr_end = start_at
                        if tr_end > pe_n:
                            desc = _travel_block_description(
                                "[移動] 前現場→現場", loc, addr
                            )
                            ok_t, eid_t = insert_calendar_event(
                                creds,
                                cal_id,
                                "[移動] 前現場→現場",
                                pe_n,
                                tr_end,
                                location=addr,
                                description=desc,
                            )
                            if ok_t:
                                travel_before_eid = str(eid_t).strip()
                                if travel_before_eid:
                                    _append_ref(
                                        new_refs,
                                        kind=kind,
                                        ref_id=ref_id,
                                        calendar_id=cal_id,
                                        event_id=travel_before_eid,
                                    )
                                    messages.append(
                                        f"{label}: 移動（前現場→現場）をカレンダーに登録しました。"
                                    )
                                else:
                                    ok_all = False
                                    messages.append(
                                        f"{label}: 移動（前現場→現場）の登録に失敗（イベントIDが空）"
                                    )
                            else:
                                ok_all = False
                                messages.append(
                                    f"{label}: 移動（前現場→現場）の登録に失敗 — {eid_t}"
                                )

    # 車両カレンダーに「前」予定が無いと上記がスキップされる → 候補検索の移動時間で補完
    if (
        kind == "vehicle"
        and travel_before_eid is None
        and addr
        and maps_api_key_configured()
        and candidate
    ):
        mx = candidate.get("travel_to_site_minutes_max")
        if mx is not None:
            try:
                m = float(mx)
            except (TypeError, ValueError):
                m = 0.0
            if m > 0:
                tr_end = start_at
                tr_start = tr_end - timedelta(minutes=m)
                day_floor = datetime.combine(start_at.date(), time.min)
                if tr_start < day_floor:
                    tr_start = day_floor
                if tr_end > tr_start:
                    office = str((settings or {}).get("office_address") or "").strip()
                    extra = (
                        f"\n（候補検索の最大移動 約{m:.0f} 分。車両カレンダーに前件が無い場合の目安）"
                    )
                    if office:
                        desc = (
                            "[移動] 前→現場（目安）\n"
                            f"出発〜到着: 拠点付近〜現場\n拠点: {office}\n現場: {addr}"
                            + extra
                        )
                    else:
                        desc = (
                            "[移動] 前→現場（目安）\n"
                            f"到着: {addr}"
                            + extra
                            + "\n※共通設定の拠点住所が未設定のため、地図リンクは省略しています。"
                        )
                    ok_t, eid_t = insert_calendar_event(
                        creds,
                        cal_id,
                        "[移動] 前→現場（目安）",
                        tr_start,
                        tr_end,
                        location=addr,
                        description=desc,
                    )
                    if ok_t:
                        eid_fb = str(eid_t).strip()
                        if eid_fb:
                            travel_before_eid = eid_fb
                            _append_ref(
                                new_refs,
                                kind=kind,
                                ref_id=ref_id,
                                calendar_id=cal_id,
                                event_id=eid_fb,
                            )
                            messages.append(
                                f"{label}: 移動（前→現場）をカレンダーに登録しました（候補検索の移動時間）。"
                            )
                        else:
                            ok_all = False
                            messages.append(
                                f"{label}: 移動（前→現場・目安）の登録に失敗（イベントIDが空）"
                            )
                    else:
                        ok_all = False
                        messages.append(
                            f"{label}: 移動（前→現場・目安）の登録に失敗 — {eid_t}"
                        )

    ok_w, detail_w = insert_calendar_event(
        creds,
        cal_id,
        work_summary,
        start_at,
        end_at,
        location=addr,
        description=work_description,
    )
    if not ok_w:
        if travel_before_eid:
            delete_calendar_event(creds, cal_id, travel_before_eid)
            while len(new_refs) > refs_before:
                new_refs.pop()
        messages.append(f"{label}: 登録失敗 — {detail_w}")
        return False

    _append_ref(
        new_refs,
        kind=kind,
        ref_id=ref_id,
        calendar_id=cal_id,
        event_id=str(detail_w),
    )
    messages.append(f"{label}: カレンダーに登録しました。")

    if addr and maps_api_key_configured():
        nxt = get_next_event_after_cached(
            events,
            slot_end_for_cache,
            day_start=day_start_dt,
            day_end=day_end_dt,
        )
        if nxt:
            loc_n = event_location(nxt)
            if loc_n.strip():
                tr_n = travel_duration_minutes(addr, loc_n.strip())
                if tr_n is not None and tr_n > 0:
                    nb = event_time_bounds(nxt)
                    ns = nb[0] if nb else None
                    ns_n = _dt_to_naive_local(ns) if ns else None
                    travel_start = end_at
                    travel_end = travel_start + timedelta(minutes=float(tr_n))
                    if ns_n and travel_end > ns_n:
                        travel_end = ns_n
                    if travel_end > travel_start:
                        desc = _travel_block_description(
                            "[移動] 現場→次現場", addr, loc_n
                        )
                        ok_a, eid_a = insert_calendar_event(
                            creds,
                            cal_id,
                            "[移動] 現場→次現場",
                            travel_start,
                            travel_end,
                            location=loc_n.strip(),
                            description=desc,
                        )
                        if ok_a:
                            eid_after = str(eid_a).strip()
                            if eid_after:
                                _append_ref(
                                    new_refs,
                                    kind=kind,
                                    ref_id=ref_id,
                                    calendar_id=cal_id,
                                    event_id=eid_after,
                                )
                                messages.append(
                                    f"{label}: 移動（現場→次現場）をカレンダーに登録しました。"
                                )
                            else:
                                ok_all = False
                                messages.append(
                                    f"{label}: 移動（現場→次現場）の登録に失敗（イベントIDが空）"
                                )
                        else:
                            ok_all = False
                            messages.append(
                                f"{label}: 移動（現場→次現場）の登録に失敗 — {eid_a}"
                            )
        elif kind == "vehicle":
            # 車両カレンダーに「次」予定が無い → 現場→拠点（戻り）を登録
            office = str((settings or {}).get("office_address") or "").strip()
            if office:
                tr_back = travel_duration_minutes(addr, office)
                if tr_back is not None and tr_back > 0:
                    travel_start = end_at
                    travel_end = travel_start + timedelta(minutes=float(tr_back))
                    if travel_end > travel_start:
                        desc = _travel_block_description(
                            "[移動] 現場→拠点（戻り）", addr, office
                        )
                        ok_a, eid_a = insert_calendar_event(
                            creds,
                            cal_id,
                            "[移動] 現場→拠点（戻り）",
                            travel_start,
                            travel_end,
                            location=office,
                            description=desc
                            + "\n（車両カレンダーに次件が無い場合。共通設定の拠点住所を使用）",
                        )
                        if ok_a:
                            eid_after = str(eid_a).strip()
                            if eid_after:
                                _append_ref(
                                    new_refs,
                                    kind=kind,
                                    ref_id=ref_id,
                                    calendar_id=cal_id,
                                    event_id=eid_after,
                                )
                                messages.append(
                                    f"{label}: 移動（現場→拠点）をカレンダーに登録しました。"
                                )
                            else:
                                ok_all = False
                                messages.append(
                                    f"{label}: 移動（現場→拠点）の登録に失敗（イベントIDが空）"
                                )
                        else:
                            ok_all = False
                            messages.append(
                                f"{label}: 移動（現場→拠点）の登録に失敗 — {eid_a}"
                            )

    return ok_all


def _description_with_candidate_extras(
    base_description: str,
    candidate: Dict[str, Any],
) -> str:
    """候補検索で付与された移動・資材メタを説明欄に追記."""
    parts: List[str] = [base_description]
    tw = candidate.get("travel_to_site_minutes_by_worker") or {}
    if isinstance(tw, dict) and tw:
        seg = "、".join(f"{wid}: 約{minutes}分" for wid, minutes in sorted(tw.items()))
        parts.append(f"【移動（前現場→現場）】{seg}")
    mx = candidate.get("travel_to_site_minutes_max")
    if mx is not None:
        try:
            parts.append(f"（最大移動 約{float(mx):.0f}分）")
        except (TypeError, ValueError):
            pass
    mc = candidate.get("material_completed_events_count")
    if mc is not None:
        parts.append(f"【資材】当日終了済み件数（代表車両カレンダー）: {mc} 件")
    me = candidate.get("material_extra_minutes")
    if me is not None:
        try:
            if float(me) > 0:
                parts.append(f"【資材ルール】追加拘束（拠点戻り等）の目安: 約{float(me):.0f}分")
        except (TypeError, ValueError):
            pass
    return "\n".join(parts)


def _delete_stored_calendar_events(
    *,
    project: Dict[str, Any],
    worker_by_id: Dict[str, Dict[str, Any]],
    vehicle_by_id: Dict[str, Dict[str, Any]],
    session_tokens: Optional[Dict[str, Any]],
    settings: Optional[Dict[str, Any]],
    vehicle_fleet_session: Optional[Dict[str, Any]],
) -> Tuple[List[str], bool]:
    """案件に保存済みの google_calendar_event_refs を削除（再確定時用）。

    戻り値は (メッセージ行, すべて削除できたか)。参照が空なら (空, True)。
    認証なしスキップ・API 失敗があると第2戻り値は False。
    """
    out: List[str] = []
    raw = project.get("google_calendar_event_refs")
    if not isinstance(raw, list) or not raw:
        return out, True
    all_ok = True
    for ref in raw:
        if not isinstance(ref, dict):
            continue
        kind = str(ref.get("kind") or "")
        ref_id = str(ref.get("ref_id") or "")
        cal_id = str(ref.get("calendar_id") or "").strip()
        ev_id = str(ref.get("event_id") or "").strip()
        if not cal_id or not ev_id:
            continue
        creds = None
        label = ref_id
        if kind == "worker":
            w = worker_by_id.get(ref_id)
            if w:
                creds = _worker_credentials(w, session_tokens)
                label = str(w.get("name") or ref_id)
        elif kind == "vehicle":
            v = vehicle_by_id.get(ref_id)
            if v:
                creds = _vehicle_calendar_credentials(
                    v, session_tokens, settings, vehicle_fleet_session
                )
                label = str(v.get("name") or ref_id)
        if not creds:
            out.append(f"既存予定の削除をスキップ（{kind} {label}: 認証なし）")
            all_ok = False
            continue
        ok, err = delete_calendar_event(creds, cal_id, ev_id)
        if not ok:
            out.append(f"既存予定の削除失敗（{kind} {label}）: {err}")
            all_ok = False
    return out, all_ok


def remove_project_schedule_from_google(
    *,
    project: Dict[str, Any],
    workers: List[Dict[str, Any]],
    vehicles: List[Dict[str, Any]],
    session_tokens: Optional[Dict[str, Any]],
    settings: Optional[Dict[str, Any]],
    vehicle_fleet_session: Optional[Dict[str, Any]],
    current_user_name: Optional[str],
) -> Tuple[List[str], bool]:
    """Google カレンダー上の案件紐づけ予定を削除し、案件の予定日時・イベント参照をクリアする。

    参照がないが予定日時だけ残っている場合も、予定日時はクリアする。
    カレンダー削除がすべて成功したときだけ案件を更新する（部分失敗時は案件は変更しない）。
    """
    worker_by_id = {str(w.get("worker_id")): w for w in workers}
    vehicle_by_id = {str(v.get("vehicle_id")): v for v in vehicles}
    msgs, del_ok = _delete_stored_calendar_events(
        project=project,
        worker_by_id=worker_by_id,
        vehicle_by_id=vehicle_by_id,
        session_tokens=session_tokens,
        settings=settings,
        vehicle_fleet_session=vehicle_fleet_session,
    )
    if not del_ok:
        msgs.append(
            "※ Google カレンダーの削除がすべて完了しなかったため、案件の予定日時は変更していません。"
        )
        return msgs, False

    pid = str(project.get("project_id") or "").strip()
    if not pid:
        msgs.append("案件IDが取得できません。")
        return msgs, False

    try:
        patch_project_fields(
            pid,
            {
                "scheduled_start_at": "",
                "scheduled_end_at": "",
                "google_calendar_event_refs": [],
            },
            current_user_name=current_user_name,
        )
    except Exception as e:
        msgs.append(f"案件の予定日時のクリアに失敗しました: {e}")
        return msgs, False

    msgs.append("案件の予定日時を削除しました。")
    return msgs, True


def commit_candidate_to_calendars(
    *,
    project: Dict[str, Any],
    candidate: Dict[str, Any],
    workers: List[Dict[str, Any]],
    vehicles: List[Dict[str, Any]],
    session_tokens: Optional[Dict[str, Any]],
    settings: Optional[Dict[str, Any]],
    vehicle_fleet_session: Optional[Dict[str, Any]],
) -> Tuple[bool, List[str], bool, List[Dict[str, Any]]]:
    """候補の時間帯で、割当職人・車両の各カレンダーに予定を1件ずつ追加する。

    戻り値は (すべて成功したか, メッセージ行, 案件に予定日時を保存してよいか, 今回登録したイベント参照)。
    再確定時は案件に保存された google_calendar_event_refs を先に削除する。
    """
    worker_by_id = {str(w.get("worker_id")): w for w in workers}
    vehicle_by_id = {str(v.get("vehicle_id")): v for v in vehicles}

    start_at: datetime = candidate["start_at"]
    end_at: datetime = candidate.get("end_at") or start_at
    if start_at.tzinfo is not None:
        start_at = start_at.astimezone(TZ).replace(tzinfo=None)
    if end_at.tzinfo is not None:
        end_at = end_at.astimezone(TZ).replace(tzinfo=None)

    pname = str(project.get("project_name") or "案件").strip() or "案件"
    addr = str(project.get("address") or "").strip()
    customer = str(project.get("customer_name") or "").strip()
    summary = f"[現場] {pname}"
    desc_parts = [f"案件: {pname}"]
    if customer:
        desc_parts.append(f"顧客: {customer}")
    if addr:
        desc_parts.append(f"住所: {addr}")
    note = str(project.get("note") or "").strip()
    if note:
        desc_parts.append(f"備考: {note}")
    base_description = "\n".join(desc_parts)
    description = _description_with_candidate_extras(base_description, candidate)

    messages: List[str] = []

    ok_all = True
    any_calendar_registered = False
    new_refs: List[Dict[str, Any]] = []

    for wid in candidate.get("worker_ids") or []:
        w = worker_by_id.get(str(wid))
        label = str(w.get("name", wid)) if w else str(wid)
        if not w:
            ok_all = False
            messages.append(f"職人 {label}: マスタに存在しません。")
            continue
        cal_id = str(w.get("calendar_id") or "").strip()
        if not cal_id:
            ok_all = False
            messages.append(f"職人 {label}: GoogleカレンダーIDが未設定です。")
            continue
        creds = _worker_credentials(w, session_tokens)
        if not creds:
            ok_all = False
            messages.append(f"職人 {label}: Google連携（OAuth）がありません。")
            continue
        ok_entity = _insert_work_and_travel_blocks(
            creds=creds,
            cal_id=cal_id,
            kind="worker",
            ref_id=str(wid),
            label=f"職人 {label}",
            start_at=start_at,
            end_at=end_at,
            work_summary=summary,
            work_description=description,
            project_addr=addr,
            messages=messages,
            new_refs=new_refs,
            candidate=candidate,
            settings=settings,
        )
        if ok_entity:
            any_calendar_registered = True
        else:
            ok_all = False

    for vid in candidate.get("vehicle_ids") or []:
        v = vehicle_by_id.get(str(vid))
        label = str(v.get("name", vid)) if v else str(vid)
        if not v:
            ok_all = False
            messages.append(f"車両 {label}: マスタに存在しません。")
            continue
        cal_id = str(v.get("calendar_id") or "").strip()
        if not cal_id:
            ok_all = False
            messages.append(f"車両 {label}: GoogleカレンダーIDが未設定です。")
            continue
        creds = _vehicle_calendar_credentials(
            v, session_tokens, settings, vehicle_fleet_session
        )
        if not creds:
            ok_all = False
            messages.append(f"車両 {label}: Google連携（OAuth）がありません。")
            continue
        ok_entity = _insert_work_and_travel_blocks(
            creds=creds,
            cal_id=cal_id,
            kind="vehicle",
            ref_id=str(vid),
            label=f"車両 {label}",
            start_at=start_at,
            end_at=end_at,
            work_summary=summary,
            work_description=description,
            project_addr=addr,
            messages=messages,
            new_refs=new_refs,
            candidate=candidate,
            settings=settings,
        )
        if ok_entity:
            any_calendar_registered = True
        else:
            ok_all = False

    if not candidate.get("worker_ids") and not candidate.get("vehicle_ids"):
        messages.append("割当職人・車両がありません。カレンダーに登録できません。")
        return False, messages, False, []

    # 全件登録できたときだけ、案件に紐づく「前回の」Google 予定を削除（部分失敗時に既存だけ消えるのを防ぐ）
    if ok_all:
        del_msgs, _ = _delete_stored_calendar_events(
            project=project,
            worker_by_id=worker_by_id,
            vehicle_by_id=vehicle_by_id,
            session_tokens=session_tokens,
            settings=settings,
            vehicle_fleet_session=vehicle_fleet_session,
        )
        messages.extend(del_msgs)
    elif project.get("google_calendar_event_refs"):
        messages.append(
            "※ 登録がすべて成功しなかったため、Google 上の「以前の予定」は自動削除していません。"
            "日付変更の重複がある場合はカレンダー側で手動削除してください。"
        )

    return ok_all, messages, any_calendar_registered, new_refs
