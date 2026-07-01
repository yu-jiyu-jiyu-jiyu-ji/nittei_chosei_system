"""カレンダー情報収集 UI（案件一覧・候補検索から利用）."""

from __future__ import annotations

import html
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import streamlit as st

from services.calendar_collect_service import (
    collect_worker_calendar_rows_for_week,
    sunday_week_containing,
)
from utils.loading_util import visible_spinner
from utils.project_register_ui import copy_calendar_event_to_register

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


_WEEKDAY_LABELS = ["日", "月", "火", "水", "木", "金", "土"]


def _week_date_filter_options(week_start: date) -> List[Tuple[str, Optional[date]]]:
    """対象週の日付絞り込み選択肢（先頭は全件）."""
    week_start = sunday_week_containing(week_start)
    options: List[Tuple[str, Optional[date]]] = [("（全ての日付）", None)]
    for i in range(7):
        d = week_start + timedelta(days=i)
        label = f"{d.strftime('%Y/%m/%d')}（{_WEEKDAY_LABELS[i]}）"
        options.append((label, d))
    return options


def _row_date(row: Dict[str, Any]) -> Optional[date]:
    start_at = row.get("start_at")
    if isinstance(start_at, datetime):
        return start_at.date()
    raw = str(row.get("date") or "").strip()
    for fmt in ("%Y/%m/%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _filter_rows(
    rows: List[Dict[str, Any]],
    *,
    query: str,
    worker_labels: List[str],
    filter_date: Optional[date] = None,
) -> List[Dict[str, Any]]:
    q = (query or "").strip().casefold()
    worker_set = set(worker_labels or [])
    out: List[Dict[str, Any]] = []
    for row in rows:
        if worker_set and row.get("worker_label") not in worker_set:
            continue
        if filter_date is not None:
            row_d = _row_date(row)
            if row_d != filter_date:
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


def _time_range_label(row: Dict[str, Any]) -> str:
    start_s = str(row.get("start_time") or "")
    end_s = str(row.get("end_time") or "")
    if start_s == "終日":
        return "終日"
    if start_s and end_s:
        return f"{start_s} - {end_s}"
    return start_s or end_s or "—"


def _inject_compact_row_css() -> None:
    st.markdown(
        """
<style>
.cal-collect-entry { display: none; }
div[data-testid="stVerticalBlockBorderWrapper"]:has(.cal-collect-entry) {
  padding: 0.12rem 0.3rem !important;
  margin-bottom: 0.18rem !important;
}
div[data-testid="stVerticalBlockBorderWrapper"]:has(.cal-collect-entry)
  div[data-testid="stHorizontalBlock"] {
  align-items: stretch !important;
}
div[data-testid="stVerticalBlockBorderWrapper"]:has(.cal-collect-entry)
  div[data-testid="column"]:first-child {
  display: flex !important;
  align-items: stretch !important;
}
div[data-testid="stVerticalBlockBorderWrapper"]:has(.cal-collect-entry)
  div[data-testid="column"]:first-child
  > div[data-testid="stVerticalBlock"] {
  flex: 1;
  display: flex;
  align-items: center;
  width: 100%;
}
div[data-testid="stVerticalBlockBorderWrapper"]:has(.cal-collect-entry)
  div[data-testid="column"]:first-child
  div[data-testid="stMarkdownContainer"] {
  width: 100%;
}
div[data-testid="stVerticalBlockBorderWrapper"]:has(.cal-collect-entry)
  div[data-testid="column"]:last-child {
  display: flex !important;
  align-items: stretch !important;
}
div[data-testid="stVerticalBlockBorderWrapper"]:has(.cal-collect-entry)
  div[data-testid="column"]:last-child
  > div[data-testid="stVerticalBlock"] {
  flex: 1;
  display: flex;
  align-items: stretch;
  width: 100%;
}
div[data-testid="stVerticalBlockBorderWrapper"]:has(.cal-collect-entry)
  div[data-testid="column"]:last-child
  div[data-testid="stButton"] {
  flex: 1;
  display: flex;
  margin: 0 !important;
}
div[data-testid="stVerticalBlockBorderWrapper"]:has(.cal-collect-entry)
  div[data-testid="column"]:last-child
  div[data-testid="stButton"] > button {
  flex: 1;
  width: 100%;
  min-height: 3rem !important;
  height: auto !important;
  padding: 0 0.45rem !important;
  font-size: 0.8rem !important;
  line-height: 1.1 !important;
}
.cal-inline-row {
  width: 100%;
  min-height: 3rem;
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: flex-start;
  gap: 0.08rem;
  overflow: hidden;
}
.cal-inline-meta {
  font-size: 0.76rem;
  line-height: 1.2;
  color: #6b7280;
  white-space: nowrap;
  overflow-x: auto;
  max-width: 100%;
  -webkit-overflow-scrolling: touch;
}
.cal-inline-title {
  font-size: 1rem;
  font-weight: 700;
  line-height: 1.25;
  color: #111827;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 100%;
}
.cal-inline-sep { color: #9ca3af; }
@media (max-width: 768px) {
  div[data-testid="stVerticalBlockBorderWrapper"]:has(.cal-collect-entry)
    div[data-testid="column"]:last-child
    div[data-testid="stButton"] > button {
    min-height: 2.85rem !important;
    font-size: 0.76rem !important;
  }
  .cal-inline-row { min-height: 2.85rem; }
  .cal-inline-meta { font-size: 0.72rem; }
  .cal-inline-title { font-size: 0.94rem; }
}
</style>
        """,
        unsafe_allow_html=True,
    )


def _build_card_body_html(row: Dict[str, Any]) -> str:
    title = html.escape(str(row.get("title") or ""))
    date_s = html.escape(str(row.get("date") or ""))
    time_s = html.escape(_time_range_label(row))
    worker = html.escape(str(row.get("worker_label") or ""))
    location = html.escape(str(row.get("location") or "").strip() or "—")
    members = html.escape(str(row.get("members") or "").strip() or "—")
    sep = '<span class="cal-inline-sep">·</span>'
    meta = f"{date_s}{sep}{time_s}{sep}{worker}{sep}{location}{sep}{members}"
    return (
        f'<div class="cal-inline-row">'
        f'<div class="cal-inline-meta">{meta}</div>'
        f'<div class="cal-inline-title">{title}</div>'
        f"</div>"
    )


def _render_collect_table(filtered_rows: List[Dict[str, Any]]) -> None:
    if not filtered_rows:
        st.info("表示する予定がありません。")
        return

    _inject_compact_row_css()
    for idx, row in enumerate(filtered_rows):
        title = str(row.get("title") or "")
        address = str(row.get("location") or "")
        with st.container(border=True):
            st.markdown('<div class="cal-collect-entry"></div>', unsafe_allow_html=True)
            line_col, btn_col = st.columns([8, 1], gap="small", vertical_alignment="center")
            with line_col:
                st.markdown(_build_card_body_html(row), unsafe_allow_html=True)
            with btn_col:
                st.button(
                    "複製",
                    type="primary",
                    key=f"cal_collect_copy_{idx}",
                    use_container_width=True,
                    on_click=copy_calendar_event_to_register,
                    kwargs={
                        "key_prefix": REGISTER_KEY_PREFIX,
                        "project_name": title,
                        "address": address,
                    },
                )


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
            st.session_state.pop("calendar_collect_date_filter", None)
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
            st.session_state.pop("calendar_collect_date_filter", None)
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
    date_options = _week_date_filter_options(week_start)
    date_labels = [label for label, _ in date_options]

    filt1, filt2 = st.columns(2)
    with filt1:
        search_q = st.text_input(
            "検索（タイトル・場所・メンバーなど）",
            key="calendar_collect_search",
            placeholder="キーワードで絞り込み…",
        )
    with filt2:
        selected_date_label = st.selectbox(
            "日付で絞り込み",
            options=date_labels,
            key="calendar_collect_date_filter",
        )
    worker_filter = st.multiselect(
        "職人で絞り込み",
        options=worker_options,
        key="calendar_collect_worker_filter",
        placeholder="（全員）",
    )

    filter_date = next((d for label, d in date_options if label == selected_date_label), None)
    filtered = _filter_rows(
        rows,
        query=search_q,
        worker_labels=worker_filter,
        filter_date=filter_date,
    )
    st.caption(f"表示件数: {len(filtered)} / {len(rows)} 件")
    _render_collect_table(filtered)
