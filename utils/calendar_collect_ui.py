"""カレンダー情報収集 UI（案件一覧・候補検索から利用）."""

from __future__ import annotations

import html
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

import streamlit as st

from services.calendar_collect_service import (
    collect_worker_calendar_rows_for_week,
    sunday_week_containing,
)
from utils.loading_util import visible_spinner
from utils.project_register_ui import apply_project_register_draft

CALENDAR_COLLECT_ACTIVE_KEY = "calendar_collect_active"
CALENDAR_COLLECT_WEEK_KEY = "calendar_collect_week_start"
CALENDAR_COLLECT_ROWS_KEY = "calendar_collect_rows"
CALENDAR_COLLECT_CACHE_WEEK_KEY = "calendar_collect_cache_week"
REGISTER_KEY_PREFIX = "project_list_new"


def navigate_to_project_list_calendar_collect() -> None:
    """候補検索から案件一覧の収集 UI を開く."""
    st.session_state[CALENDAR_COLLECT_ACTIVE_KEY] = True
    if CALENDAR_COLLECT_WEEK_KEY not in st.session_state:
        st.session_state[CALENDAR_COLLECT_WEEK_KEY] = sunday_week_containing(date.today())
    st.switch_page("pages/01_案件一覧.py")


def _week_label(week_start: date) -> str:
    week_end = week_start + timedelta(days=6)
    return f"{week_start.strftime('%Y/%m/%d')} 〜 {week_end.strftime('%Y/%m/%d')}"


def _filter_rows(
    rows: List[Dict[str, Any]],
    *,
    query: str,
    worker_labels: List[str],
) -> List[Dict[str, Any]]:
    q = (query or "").strip().casefold()
    worker_set = set(worker_labels or [])
    out: List[Dict[str, Any]] = []
    for row in rows:
        if worker_set and row.get("worker_label") not in worker_set:
            continue
        if not q:
            out.append(row)
            continue
        hay = " ".join(
            [
                str(row.get("date") or ""),
                str(row.get("title") or ""),
                str(row.get("start_time") or ""),
                str(row.get("end_time") or ""),
                str(row.get("location") or ""),
                str(row.get("members") or ""),
                str(row.get("worker_label") or ""),
            ]
        ).casefold()
        if q in hay:
            out.append(row)
    return out


def _fetch_and_cache_rows(
    *,
    week_start: date,
    workers: List[Dict[str, Any]],
    session_tokens: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    week_start = sunday_week_containing(week_start)
    with visible_spinner("カレンダー情報を収集中…"):
        rows, warnings = collect_worker_calendar_rows_for_week(
            week_start=week_start,
            workers=workers,
            session_tokens=session_tokens,
        )
    st.session_state[CALENDAR_COLLECT_ROWS_KEY] = rows
    st.session_state[CALENDAR_COLLECT_CACHE_WEEK_KEY] = week_start.isoformat()
    for msg in warnings:
        st.warning(msg)
    return rows


def _ensure_rows_loaded(
    *,
    week_start: date,
    workers: List[Dict[str, Any]],
    session_tokens: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    week_start = sunday_week_containing(week_start)
    cache_week = str(st.session_state.get(CALENDAR_COLLECT_CACHE_WEEK_KEY) or "")
    rows = st.session_state.get(CALENDAR_COLLECT_ROWS_KEY)
    if cache_week == week_start.isoformat() and isinstance(rows, list):
        return rows
    return _fetch_and_cache_rows(
        week_start=week_start,
        workers=workers,
        session_tokens=session_tokens,
    )


def _inject_collect_card_css() -> None:
    st.markdown(
        """
<style>
.cal-collect-list { display:flex; flex-direction:column; gap:0.55rem; }
.cal-collect-card {
    border:1px solid #e5e7eb;
    border-radius:10px;
    padding:0.65rem 0.8rem 0.45rem;
    background:#fff;
}
.cal-collect-card-head {
    display:flex;
    justify-content:space-between;
    align-items:flex-start;
    gap:0.5rem;
    margin-bottom:0.35rem;
    font-size:0.78rem;
    color:#6b7280;
    line-height:1.35;
}
.cal-collect-card-time {
    white-space:nowrap;
    font-weight:600;
    color:#4b5563;
}
.cal-collect-card-title {
    font-size:0.92rem;
    font-weight:700;
    color:#111827;
    line-height:1.4;
    margin:0 0 0.3rem 0;
}
.cal-collect-card-line {
    font-size:0.78rem;
    color:#4b5563;
    line-height:1.4;
    margin:0.1rem 0;
}
.cal-collect-card-line b { color:#6b7280; font-weight:600; }
div[data-testid="stVerticalBlock"]:has(.cal-collect-card) + div[data-testid="stVerticalBlock"] div[data-testid="stButton"] button {
    margin-top:-0.15rem;
    margin-bottom:0.35rem;
    border:none;
    background:transparent;
    color:#2563eb;
    font-weight:600;
    padding:0 0 0.2rem;
    justify-content:flex-start;
    box-shadow:none;
    min-height:2rem;
}
@media (max-width: 768px) {
    .cal-collect-list { gap:0.45rem; }
    .cal-collect-card {
        padding:0.5rem 0.65rem 0.35rem;
        border-radius:8px;
    }
    .cal-collect-card-head { font-size:0.74rem; margin-bottom:0.25rem; }
    .cal-collect-card-title { font-size:0.86rem; }
    .cal-collect-card-line { font-size:0.74rem; }
}
</style>
        """,
        unsafe_allow_html=True,
    )


def _time_range_label(row: Dict[str, Any]) -> str:
    start_s = str(row.get("start_time") or "")
    end_s = str(row.get("end_time") or "")
    if start_s == "終日":
        return "終日"
    if start_s and end_s:
        return f"{start_s} - {end_s}"
    return start_s or end_s or "—"


def _build_collect_card_html(row: Dict[str, Any]) -> str:
    date_s = html.escape(str(row.get("date") or ""))
    worker = html.escape(str(row.get("worker_label") or ""))
    title = html.escape(str(row.get("title") or ""))
    time_s = html.escape(_time_range_label(row))
    location = html.escape(str(row.get("location") or "—"))
    members = html.escape(str(row.get("members") or "—"))
    return (
        f'<div class="cal-collect-card">'
        f'<div class="cal-collect-card-head">'
        f"<span>{date_s} · {worker}</span>"
        f'<span class="cal-collect-card-time">{time_s}</span>'
        f"</div>"
        f'<p class="cal-collect-card-title">{title}</p>'
        f'<p class="cal-collect-card-line"><b>場所</b> {location}</p>'
        f'<p class="cal-collect-card-line"><b>メンバー</b> {members}</p>'
        f"</div>"
    )


def _render_collect_table(filtered_rows: List[Dict[str, Any]]) -> None:
    if not filtered_rows:
        st.info("表示する予定がありません。")
        return

    _inject_collect_card_css()
    st.markdown('<div class="cal-collect-list">', unsafe_allow_html=True)
    for row in filtered_rows:
        st.markdown(_build_collect_card_html(row), unsafe_allow_html=True)
        if st.button("複製", key=f"cal_collect_copy_{row.get('row_key')}", type="tertiary"):
            apply_project_register_draft(
                REGISTER_KEY_PREFIX,
                project_name=str(row.get("title") or ""),
                address=str(row.get("location") or ""),
            )
            st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)


def render_calendar_collect_section(
    *,
    workers: List[Dict[str, Any]],
    session_tokens: Optional[Dict[str, Any]],
) -> None:
    """案件一覧に埋め込むカレンダー情報収集ブロック."""
    active = bool(st.session_state.get(CALENDAR_COLLECT_ACTIVE_KEY, False))
    if CALENDAR_COLLECT_WEEK_KEY not in st.session_state:
        st.session_state[CALENDAR_COLLECT_WEEK_KEY] = sunday_week_containing(date.today())

    st.subheader("カレンダー情報収集")
    st.caption(
        "登録済み職人の Google カレンダーから予定を収集し、案件登録の下書きに転記できます。"
        " キャンセル済み予定は表示しません。"
    )

    if st.button(
        "カレンダー情報収集",
        type="primary",
        key="calendar_collect_open_btn",
        disabled=active,
    ):
        st.session_state[CALENDAR_COLLECT_ACTIVE_KEY] = True
        st.session_state.pop(CALENDAR_COLLECT_CACHE_WEEK_KEY, None)
        st.rerun()

    if not active:
        return

    week_start: date = st.session_state[CALENDAR_COLLECT_WEEK_KEY]
    if not isinstance(week_start, date):
        week_start = date.fromisoformat(str(week_start))
        st.session_state[CALENDAR_COLLECT_WEEK_KEY] = week_start

    nav_l, nav_c, nav_r = st.columns([1, 2, 1])
    with nav_l:
        if st.button("前週", key="calendar_collect_prev_week", use_container_width=True):
            st.session_state[CALENDAR_COLLECT_WEEK_KEY] = week_start - timedelta(days=7)
            st.session_state.pop(CALENDAR_COLLECT_CACHE_WEEK_KEY, None)
            st.rerun()
    with nav_c:
        st.markdown(
            f"<div style='text-align:center;font-size:0.9rem;'>"
            f"<b>対象週</b><br>{_week_label(week_start)}</div>",
            unsafe_allow_html=True,
        )
    with nav_r:
        if st.button("次週", key="calendar_collect_next_week", use_container_width=True):
            st.session_state[CALENDAR_COLLECT_WEEK_KEY] = week_start + timedelta(days=7)
            st.session_state.pop(CALENDAR_COLLECT_CACHE_WEEK_KEY, None)
            st.rerun()

    if st.button("閉じる", key="calendar_collect_close", type="secondary"):
        st.session_state[CALENDAR_COLLECT_ACTIVE_KEY] = False
        st.rerun()

    rows = _ensure_rows_loaded(
        week_start=week_start,
        workers=workers,
        session_tokens=session_tokens,
    )
    worker_options = sorted({str(r.get("worker_label") or "") for r in rows if r.get("worker_label")})

    search_q = st.text_input(
        "検索（タイトル・場所・メンバーなど）",
        key="calendar_collect_search",
        placeholder="キーワードで絞り込み…",
    )
    worker_filter = st.multiselect(
        "職人で絞り込み",
        options=worker_options,
        key="calendar_collect_worker_filter",
        placeholder="（全員）",
    )

    filtered = _filter_rows(rows, query=search_q, worker_labels=worker_filter)
    st.caption(f"表示件数: {len(filtered)} / {len(rows)} 件")
    _render_collect_table(filtered)
