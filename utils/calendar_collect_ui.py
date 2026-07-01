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


def _render_collect_table(filtered_rows: List[Dict[str, Any]]) -> None:
    if not filtered_rows:
        st.info("表示する予定がありません。")
        return

    st.markdown(
        """
<style>
.cal-collect-hdr { font-size:0.82rem; font-weight:700; color:#374151; padding-bottom:0.25rem; border-bottom:1px solid #e5e7eb; margin-bottom:0.35rem; }
.cal-collect-row { font-size:0.84rem; padding:0.35rem 0; border-bottom:1px solid #f3f4f6; }
</style>
        """,
        unsafe_allow_html=True,
    )
    widths = [1.0, 2.2, 0.8, 0.8, 1.8, 1.6, 0.7]
    header = st.columns(widths)
    for col, label in zip(
        header,
        ["日付", "タイトル", "開始", "終了", "場所", "メンバー", "複製"],
    ):
        col.markdown(f'<div class="cal-collect-hdr">{label}</div>', unsafe_allow_html=True)

    for row in filtered_rows:
        cols = st.columns(widths)
        cols[0].markdown(
            f'<div class="cal-collect-row">{html.escape(str(row.get("date") or ""))}</div>',
            unsafe_allow_html=True,
        )
        cols[1].markdown(
            f'<div class="cal-collect-row">{html.escape(str(row.get("title") or ""))}'
            f'<br><span style="color:#6b7280;font-size:0.75rem;">{html.escape(str(row.get("worker_label") or ""))}</span></div>',
            unsafe_allow_html=True,
        )
        cols[2].markdown(
            f'<div class="cal-collect-row">{html.escape(str(row.get("start_time") or ""))}</div>',
            unsafe_allow_html=True,
        )
        cols[3].markdown(
            f'<div class="cal-collect-row">{html.escape(str(row.get("end_time") or ""))}</div>',
            unsafe_allow_html=True,
        )
        cols[4].markdown(
            f'<div class="cal-collect-row">{html.escape(str(row.get("location") or "—"))}</div>',
            unsafe_allow_html=True,
        )
        cols[5].markdown(
            f'<div class="cal-collect-row">{html.escape(str(row.get("members") or "—"))}</div>',
            unsafe_allow_html=True,
        )
        with cols[6]:
            if st.button("複製", key=f"cal_collect_copy_{row.get('row_key')}", type="secondary"):
                apply_project_register_draft(
                    REGISTER_KEY_PREFIX,
                    project_name=str(row.get("title") or ""),
                    address=str(row.get("location") or ""),
                )
                st.success("案件名と住所を新規登録フォームへ転記しました。上部のフォームを確認してください。")
                st.rerun()


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

    nav_l, nav_c, nav_r, nav_close = st.columns([1, 3, 1, 1])
    with nav_l:
        if st.button("前週", key="calendar_collect_prev_week"):
            st.session_state[CALENDAR_COLLECT_WEEK_KEY] = week_start - timedelta(days=7)
            st.session_state.pop(CALENDAR_COLLECT_CACHE_WEEK_KEY, None)
            st.rerun()
    with nav_c:
        st.markdown(f"**対象週:** {_week_label(week_start)}（日曜始まり）")
    with nav_r:
        if st.button("次週", key="calendar_collect_next_week"):
            st.session_state[CALENDAR_COLLECT_WEEK_KEY] = week_start + timedelta(days=7)
            st.session_state.pop(CALENDAR_COLLECT_CACHE_WEEK_KEY, None)
            st.rerun()
    with nav_close:
        if st.button("閉じる", key="calendar_collect_close"):
            st.session_state[CALENDAR_COLLECT_ACTIVE_KEY] = False
            st.rerun()

    rows = _ensure_rows_loaded(
        week_start=week_start,
        workers=workers,
        session_tokens=session_tokens,
    )
    worker_options = sorted({str(r.get("worker_label") or "") for r in rows if r.get("worker_label")})

    filt_l, filt_r = st.columns([2, 1])
    with filt_l:
        search_q = st.text_input(
            "検索（タイトル・場所・メンバーなど）",
            key="calendar_collect_search",
            placeholder="キーワードで絞り込み…",
        )
    with filt_r:
        worker_filter = st.multiselect(
            "職人で絞り込み",
            options=worker_options,
            key="calendar_collect_worker_filter",
            placeholder="（全員）",
        )

    filtered = _filter_rows(rows, query=search_q, worker_labels=worker_filter)
    st.caption(f"表示件数: {len(filtered)} / {len(rows)} 件")
    _render_collect_table(filtered)
