from __future__ import annotations

import json
from collections import defaultdict
from contextlib import nullcontext
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import plotly.graph_objects as go
import streamlit.components.v1 as components
import streamlit as st
import pandas as pd

from config.constants import (
    APP_TITLE,
    DB_UNAVAILABLE_MESSAGE,
    MAX_REQUIRED_WORKERS,
    MAX_WORK_DURATION_MINUTES,
    MIN_WORK_DURATION_MINUTES,
)
from services.candidate_search_service import (
    apply_previous_location_overrides_to_calendars,
    calendar_display_day_offsets,
    collect_missing_previous_locations,
    collect_week_busy_events,
    fetch_week_calendar_events_bundle,
    week_busy_events_from_bundle,
    format_week_events_jst_table_rows,
    candidate_includes_worker_off,
    is_company_closed_day,
    search_candidates,
    sunday_week_containing,
    work_hours_display_hours,
)
from services.firestore_service import FirestoreConnectionError, FirestoreSaveError
from services.project_service import (
    create_quick_project,
    list_projects as list_projects_from_service,
    patch_project_fields,
)
from services.schedule_commit_service import (
    commit_candidate_to_calendars,
    remove_project_schedule_from_google,
)
from services.setting_service import get_settings
from services.vehicle_service import list_vehicles
from services.worker_service import list_workers
from utils.layout_util import STREAMLIT_MENU_ITEMS, inject_sidebar_nav, inject_wide_layout
from utils.loading_util import (
    candidate_search_busy_active,
    format_search_progress_pct,
    inject_clear_force_busy_overlay,
    inject_force_busy_marker,
    visible_spinner,
)
from utils.calendar_collect_ui import navigate_to_project_list_calendar_collect
from utils.combobox_util import render_searchable_selectbox, resolve_combo_selection
from utils.project_register_ui import (
    CANDIDATE_REGISTER_DIALOG_RESULT_KEY,
    render_candidate_search_register_ui,
)
from utils.session_util import apply_registered_project_to_candidate_search, init_session_state


def _on_candidate_search_button_click() -> None:
    """「検索」用 on_click。Streamlit はコールバックをスクリプト本体より先に実行するため、
    ページ先頭のマスタ取得でも「検索・カレンダー表示中…」に切り替えられる。"""
    st.session_state["_candidate_search_btn_pressed"] = True
    st.session_state["candidate_search_ui_busy"] = True
    masters = st.session_state.get("_candidate_search_masters") or {}
    projects = masters.get("projects") if isinstance(masters.get("projects"), list) else []
    if projects:
        names = [
            str(p.get("project_name") or "").strip()
            for p in projects
            if str(p.get("project_name") or "").strip()
        ]
        resolved = resolve_combo_selection(
            "candidate_search_project_select",
            names,
            query_key="candidate_search_project_query",
        )
        if resolved:
            for p in projects:
                if str(p.get("project_name") or "").strip() == resolved:
                    try:
                        rw = int(p.get("required_workers") or 0)
                        if rw > 0:
                            st.session_state["candidate_search_capacity"] = rw
                    except (TypeError, ValueError):
                        pass
                    break


def _clear_candidate_search_ui_busy() -> None:
    st.session_state.pop("candidate_search_ui_busy", None)


def _sanitize_stale_candidate_search_busy(*, starting_search: bool = False) -> None:
    """ジョブ無しで busy だけ残るとオーバーレイが消えない。検索開始直前は ui_busy を消さない."""
    if (
        starting_search
        or st.session_state.get("candidate_search_job") is not None
        or st.session_state.get("candidate_search_display_pending")
    ):
        return
    if st.session_state.get("candidate_search_calendar_pending"):
        st.session_state.pop("candidate_search_calendar_pending", None)
    if st.session_state.get("candidate_search_ui_busy"):
        _clear_candidate_search_ui_busy()
        inject_clear_force_busy_overlay()


def _inject_candidate_search_busy_if_needed() -> None:
    """分割検索〜カレンダー描画完了まで全画面オーバーレイを表示."""
    if st.session_state.get("candidate_search_display_pending"):
        inject_force_busy_marker("カレンダー表示中…")
        return
    cjob = st.session_state.get("candidate_search_job")
    if not cjob:
        return
    step_top = int(cjob.get("step", -99))
    n_top = len(cjob.get("day_offsets") or calendar_display_day_offsets())
    if step_top == -1:
        busy_msg = "カレンダー取得中…"
    elif step_top < n_top:
        busy_msg = f"検索中…（{format_search_progress_pct(step_top, n_top)}）"
    else:
        return
    inject_force_busy_marker(busy_msg)


def _begin_candidate_search_display_phase() -> None:
    """分割検索完了後、Plotly カレンダー描画までオーバーレイを維持する."""
    st.session_state["candidate_search_display_pending"] = True
    st.session_state["candidate_search_ui_busy"] = True


def _finish_candidate_search_display_if_needed() -> None:
    """カレンダー表示完了後にオーバーレイを解除（未設定なら何もしない）."""
    if not st.session_state.pop("candidate_search_display_pending", None):
        return
    _clear_candidate_search_ui_busy()
    inject_clear_force_busy_overlay()


def _parse_calendar_component_value(
    raw: Any,
) -> tuple[Optional[str], bool, Optional[str]]:
    """components.html の戻り値を (候補ID, readyイベント, nonce) に分解する."""
    if raw is None:
        return None, False, None
    raw_s = str(raw).strip()
    if not raw_s:
        return None, False, None
    if raw_s.startswith("{"):
        try:
            payload = json.loads(raw_s)
            nonce_raw = payload.get("nonce")
            nonce = str(nonce_raw).strip() if nonce_raw is not None else None
            if str(payload.get("event") or "").strip() == "ready":
                return None, True, nonce or None
            cid = str(payload.get("cid") or "").strip() or None
            return cid, False, nonce or None
        except Exception:
            return None, False, None
    return raw_s, False, None


def _is_new_calendar_component_click(
    cal_clicked: Optional[str], click_nonce: Optional[str]
) -> bool:
    """同一候補の連続 rerun を防ぐ（nonce 優先）."""
    if not cal_clicked:
        return False
    if click_nonce:
        return click_nonce != st.session_state.get("_cal_last_component_nonce")
    return cal_clicked != st.session_state.get("_cal_last_component_click")


def _remember_calendar_component_click(
    cal_clicked: str, click_nonce: Optional[str]
) -> None:
    _purge_candidate_dialog_widget_keys()
    st.session_state["candidate_dialog_id"] = cal_clicked
    st.session_state["_cal_last_component_click"] = cal_clicked
    if click_nonce:
        st.session_state["_cal_last_component_nonce"] = click_nonce
    st.session_state.pop("candidate_search_display_pending", None)
    _clear_candidate_search_ui_busy()


_YOUBI = ("月", "火", "水", "木", "金", "土", "日")


def _weekday_label_calendar_header(d: date) -> str:
    """週カレンダー列見出し（1行: 「3/22 日」形式。表形式カレンダーと同じ読み方）."""
    return f"{d.month}/{d.day} {_YOUBI[d.weekday()]}"


def _format_date_jp(d: date) -> str:
    """詳細ダイアログ用の日付（和文の読みやすい表記）."""
    return f"{d.year}年{d.month}月{d.day}日（{_YOUBI[d.weekday()]}）"


def _format_week_range_short(ws: date) -> str:
    """週ナビ用: 日曜始まりの7日間（例: 5/18（日）〜5/24（土））."""
    we = ws + timedelta(days=6)
    return f"{ws.month}/{ws.day}（{_YOUBI[ws.weekday()]}）〜{we.month}/{we.day}（{_YOUBI[we.weekday()]}）"


def _format_slot_card_label(start_at: datetime, end_at: datetime) -> str:
    """空きカード見出し（例: 08/12（水） 10:00〜12:00）."""
    d = start_at.date()
    return (
        f"{d.month:02d}/{d.day:02d}（{_YOUBI[d.weekday()]}） "
        f"{start_at.strftime('%H:%M')}〜{end_at.strftime('%H:%M')}"
    )


_DURATION_OPTIONS: List[int] = list(range(MIN_WORK_DURATION_MINUTES, MAX_WORK_DURATION_MINUTES + 1, 30))
_WEEK_OFFSET_OPTIONS: List[Tuple[int, str]] = [
    (0, "今週"),
    (1, "来週"),
    (2, "再来週"),
    (3, "翌々週"),
]


def _build_search_project(
    selected_project: Optional[Dict[str, Any]],
    *,
    required_capacity: int,
    work_duration_minutes: int,
) -> Dict[str, Any]:
    """案件未選択でも人数・作業時間だけで検索できるよう、検索用 dict を組み立てる."""
    duration = max(MIN_WORK_DURATION_MINUTES, int(work_duration_minutes or 120))
    capacity = max(0, int(required_capacity or 0))
    if selected_project:
        out = dict(selected_project)
        out["work_duration_minutes"] = duration
        if capacity > 0:
            out["required_workers"] = capacity
        return out
    return {
        "project_id": "",
        "project_name": "",
        "customer_name": "",
        "address": "",
        "work_duration_minutes": duration,
        "required_workers": capacity,
        "required_vehicle_count": None,
        "note": "",
    }


def _open_candidate_dialog_from_card(candidate_id: str) -> None:
    """空きカードから予約ダイアログを開く."""
    cid = str(candidate_id or "").strip()
    if not cid:
        return
    _purge_candidate_dialog_widget_keys()
    st.session_state["candidate_dialog_id"] = cid
    st.session_state["_cal_last_component_click"] = cid
    st.session_state["_cal_last_component_nonce"] = f"card_{cid}_{datetime.now().timestamp():.6f}"
    st.session_state.pop("candidate_search_display_pending", None)
    _clear_candidate_search_ui_busy()


def _render_free_slot_cards(
    candidates: List[Dict[str, Any]],
    *,
    worker_id_to_name: Dict[str, str],
) -> None:
    """近い空きからカード一覧を表示する."""
    if not candidates:
        return
    st.caption("条件に合う空き日程です。カードを開くと直前・直後の所在を確認し、カレンダー登録できます。")
    for idx, c in enumerate(candidates):
        start_at = c.get("start_at")
        end_at = c.get("end_at") or start_at
        if not isinstance(start_at, datetime) or not isinstance(end_at, datetime):
            continue
        cid = str(c.get("candidate_id") or "")
        workers_text = "、".join(
            worker_id_to_name.get(str(wid), str(wid)) for wid in (c.get("worker_ids") or [])
        ) or "—"
        with st.container(border=True):
            c1, c2 = st.columns([4.2, 1.2])
            with c1:
                st.markdown(f"**{_format_slot_card_label(start_at, end_at)}**")
                st.caption(f"人数 {c.get('capacity') or '—'}｜職人 {workers_text}")
            with c2:
                if st.button("開く", key=f"free_slot_open_{cid}_{idx}", use_container_width=True):
                    _open_candidate_dialog_from_card(cid)
                    st.rerun()


def _sunday_week_from_today(week_offset: int) -> date:
    """今日を含む週の日曜 + week_offset 週（0=今週, 1=来週, 2=再来週）."""
    return sunday_week_containing(date.today()) + timedelta(days=7 * week_offset)


def _has_candidate_search_results() -> bool:
    return "candidate_results" in st.session_state


def _go_to_calendar_week(ws: date, *, trigger_research: bool) -> None:
    """表示週を切り替え。trigger_research 時はその週7日分で候補検索を開始."""
    st.session_state["candidate_calendar_week_start"] = ws
    st.session_state.pop(PLOTLY_CALENDAR_KEY, None)
    for _ck in list(st.session_state.keys()):
        if isinstance(_ck, str) and _ck.startswith("calendar_week_events_"):
            st.session_state.pop(_ck, None)
    if trigger_research:
        st.session_state["week_nav_trigger_search"] = True
        st.session_state["candidate_search_ui_busy"] = True
    else:
        st.session_state["week_calendar_browse"] = True
    st.rerun()


def _is_jp_public_holiday(d: date) -> bool:
    """日本の祝日（振替・国民の休日を含む）。jpholiday が無い場合は常に False."""
    try:
        import jpholiday

        return bool(jpholiday.is_holiday(d))
    except Exception:
        return False


def _column_bg_color(d: date) -> str:
    """週カレンダー1列分の背景色（平日白・土曜水色・日曜・祝は薄赤）."""
    if _is_jp_public_holiday(d):
        return "#ffe8e8"
    wd = d.weekday()
    if wd == 6:  # Sunday
        return "#ffe8e8"
    if wd == 5:  # Saturday
        return "#e6f7ff"
    return "#ffffff"


# カレンダー: 時刻軸余白（日付ヘッダー・Plotly と揃える）
_CALENDAR_MARGIN_LEFT = 62
_CALENDAR_MARGIN_RIGHT = 18
# 表示枠に収める日数（7日分の実幅 = 表示枠 × 7/3 → 4日目以降は横スクロール）
_CALENDAR_VIEWPORT_DAYS = 3
_CALENDAR_WEEK_DAYS = 7
_CALENDAR_INNER_WIDTH_RATIO = _CALENDAR_WEEK_DAYS / _CALENDAR_VIEWPORT_DAYS


def _build_calendar_header_html(week_dates: List[date]) -> str:
    """週見出し行 HTML（カレンダーコンポーネント用）。"""
    n = len(week_dates)
    parts: List[str] = []
    for i, d in enumerate(week_dates):
        label = _weekday_label_calendar_header(d)
        bg = _column_bg_color(d)
        border = "border-right:1px solid #d8d8d8;" if i < n - 1 else ""
        parts.append(
            f'<div class="cal-head-cell" style="background:{bg};{border}">{label}</div>'
        )
    inner = "".join(parts)
    return (
        '<div class="candidate-cal-header-row">'
        '<div class="candidate-cal-y-axis-gutter" aria-hidden="true"></div>'
        f'<div class="candidate-cal-day-grid" style="grid-template-columns:repeat({n},minmax(0,1fr));">'
        f"{inner}"
        "</div>"
        '<div class="candidate-cal-margin-right" aria-hidden="true"></div>'
        "</div>"
    )


def _render_calendar_scroll_component(
    fig: go.Figure,
    header_html: str,
    *,
    plot_height: int,
    notify_when_ready: bool = False,
) -> tuple[Optional[str], bool, Optional[str]]:
    """日付＋Plotly を1つの横スクロール枠に描画（3日幅・7日分は枠内スクロール）。"""
    fig_dict = json.loads(fig.to_json())
    data_js = json.dumps(fig_dict.get("data", []), ensure_ascii=False)
    layout_js = json.dumps(fig_dict.get("layout", {}), ensure_ascii=False)
    ratio = _CALENDAR_INNER_WIDTH_RATIO
    margin_l = _CALENDAR_MARGIN_LEFT
    margin_r = _CALENDAR_MARGIN_RIGHT
    header_h = 52
    frame_h = int(plot_height) + header_h + 24
    cal_min_h = int(plot_height) + header_h
    notify_ready_js = "true" if notify_when_ready else "false"

    clicked = components.html(
        f"""
<style>
html, body {{
  margin: 0; padding: 0;
}}
.candidate-cal-scroll-host {{
  width: 100%; max-width: 100%; overflow: hidden; box-sizing: border-box;
  min-height: {cal_min_h}px;
}}
.candidate-cal-scroll-x {{
  width: 100%; max-width: 100%;
  min-height: {cal_min_h}px;
  overflow-x: auto; overflow-y: visible;
  -webkit-overflow-scrolling: touch;
  touch-action: pan-x pan-y;
  overscroll-behavior-x: contain;
}}
.candidate-cal-scroll-inner {{
  box-sizing: border-box; min-height: {cal_min_h}px;
}}
.candidate-cal-header-row {{
  display: flex; width: 100%; align-items: stretch; box-sizing: border-box;
  min-height: {header_h}px;
}}
.candidate-cal-y-axis-gutter {{
  width: {margin_l}px; min-width: {margin_l}px; flex-shrink: 0;
}}
.candidate-cal-margin-right {{
  width: {margin_r}px; min-width: {margin_r}px; flex-shrink: 0;
}}
.candidate-cal-day-grid {{
  flex: 1; display: grid; border: 1px solid #d8d8d8; border-bottom: none; box-sizing: border-box;
}}
.cal-head-cell {{
  box-sizing: border-box; text-align: center; padding: 10px 5px;
  font-size: 15px; color: #222; font-weight: 600;
}}
#candidate-cal-plot {{
  width: 100%; height: {plot_height}px; min-height: {plot_height}px;
  touch-action: pan-x pan-y; cursor: pointer;
}}
#candidate-cal-plot .plotly-graph-div {{
  touch-action: pan-x pan-y !important;
}}
@media (max-width: 767px) {{
  .cal-head-cell {{ font-size: 14px; padding: 8px 4px; }}
}}
</style>
<div class="candidate-cal-scroll-host">
  <div id="candidate-cal-scroll-x" class="candidate-cal-scroll-x">
    <div id="candidate-cal-inner" class="candidate-cal-scroll-inner">
      {header_html}
      <div id="candidate-cal-plot"></div>
    </div>
  </div>
</div>
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/@streamlit/component-lib@2.0.0/dist/index.min.js"></script>
<script>
(function() {{
  const RATIO = {ratio};
  const ML = {margin_l};
  const MR = {margin_r};
  const SHOULD_NOTIFY_READY = {notify_ready_js};
  const figData = {data_js};
  const figLayout = {layout_js};

  function syncWidth(keepScroll) {{
    const scrollX = document.getElementById("candidate-cal-scroll-x");
    const inner = document.getElementById("candidate-cal-inner");
    if (!scrollX || !inner) return;
    const prev = scrollX.scrollLeft;
    const vp = scrollX.clientWidth;
    if (vp < 1) return;
    const full = Math.round(vp * RATIO);
    inner.style.width = full + "px";
    inner.style.minWidth = full + "px";
    inner.style.maxWidth = full + "px";
    const plotEl = document.getElementById("candidate-cal-plot");
    if (plotEl && window.Plotly) {{
      try {{
        window.Plotly.relayout(plotEl, {{
          width: full,
          autosize: false,
          "margin.l": ML,
          "margin.r": MR,
        }});
      }} catch (e) {{}}
    }}
    if (!keepScroll) scrollX.scrollLeft = 0;
    else scrollX.scrollLeft = prev;
  }}

  function pickCandidateId(ev) {{
    if (!ev || !ev.points || !ev.points.length) return null;
    const cd = ev.points[0].customdata;
    const cid = Array.isArray(cd) ? cd[0] : cd;
    return cid ? String(cid) : null;
  }}

  function emitComponentClick(cid) {{
    if (!cid || !window.Streamlit) return;
    window.Streamlit.setComponentValue(JSON.stringify({{
      cid: String(cid),
      nonce: Date.now()
    }}));
  }}

  function releaseParentBusyOverlay() {{
    try {{
      var pdoc = window.parent.document;
      pdoc.querySelectorAll("#_st_force_busy_marker, ._st_force_busy_marker").forEach(function(el) {{
        el.remove();
      }});
      var S = pdoc._stGlobalBusyOverlay;
      if (S) {{
        if (S.hideSpinTimer) {{ clearTimeout(S.hideSpinTimer); S.hideSpinTimer = null; }}
        if (S.pendingTimer) {{ clearTimeout(S.pendingTimer); S.pendingTimer = null; }}
        S.state = "idle";
      }}
      var L = pdoc.getElementById("_st_global_busy_layer");
      if (L) L.classList.remove("_st_busy_on", "_st_busy_pending");
    }} catch (err) {{}}
  }}

  function notifyCalendarReady() {{
    if (!SHOULD_NOTIFY_READY) return;
    releaseParentBusyOverlay();
    // setComponentValue は呼ばない（ready で rerun すると直後に開いたダイアログが閉じる）
  }}

  function bindVerticalPageScroll(container) {{
    if (!container) return;
    let startX = 0, startY = 0;
    container.addEventListener("touchstart", function(e) {{
      if (!e.touches || e.touches.length !== 1) return;
      startX = e.touches[0].clientX;
      startY = e.touches[0].clientY;
    }}, {{passive: true}});
    container.addEventListener("touchmove", function(e) {{
      if (!e.touches || e.touches.length !== 1) return;
      const dx = e.touches[0].clientX - startX;
      const dy = e.touches[0].clientY - startY;
      if (Math.abs(dy) > 10 && Math.abs(dy) > Math.abs(dx) * 1.1) {{
        try {{
          window.parent.scrollBy(0, -dy);
          startY = e.touches[0].clientY;
        }} catch (err) {{}}
      }}
    }}, {{passive: true}});
  }}

  function bindHorizontalSwipe(scrollEl, plotEl) {{
    const targets = [scrollEl, document.getElementById("candidate-cal-inner")];
    if (plotEl) targets.push(plotEl);
    let sx = 0, sy = 0, sl = 0, swiping = false, moved = false;
    const onStart = function(e) {{
      if (!e.touches || e.touches.length !== 1) return;
      sx = e.touches[0].clientX;
      sy = e.touches[0].clientY;
      sl = scrollEl.scrollLeft;
      swiping = true;
      moved = false;
      if (plotEl) plotEl.dataset.calSwiped = "0";
    }};
    const onMove = function(e) {{
      if (!swiping || !e.touches || e.touches.length !== 1) return;
      const dx = e.touches[0].clientX - sx;
      const dy = e.touches[0].clientY - sy;
      if (Math.abs(dx) > 12 && Math.abs(dx) > Math.abs(dy) * 1.2) {{
        scrollEl.scrollLeft = sl - dx;
        moved = true;
        e.preventDefault();
      }}
    }};
    const onEnd = function() {{
      swiping = false;
      if (plotEl) plotEl.dataset.calSwiped = (moved && Math.abs(sl - scrollEl.scrollLeft) > 8) ? "1" : "0";
    }};
    targets.forEach(function(t) {{
      if (!t) return;
      t.addEventListener("touchstart", onStart, {{passive: true}});
      t.addEventListener("touchmove", onMove, {{passive: false}});
      t.addEventListener("touchend", onEnd, {{passive: true}});
      t.addEventListener("touchcancel", onEnd, {{passive: true}});
    }});
  }}

  function nearestCandidateFromPointer(gd, clientX, clientY) {{
    try {{
      var trace = (gd.data || [])[0];
      if (!trace || !trace.x || !trace.x.length) return null;
      var box = gd.getBoundingClientRect();
      var lx = clientX - box.left;
      var ly = clientY - box.top;
      var fl = gd._fullLayout;
      if (!fl || !fl.xaxis || !fl.yaxis || !fl._size) return null;
      var best = -1;
      var bestDist = Infinity;
      for (var i = 0; i < trace.x.length; i++) {{
        var px = fl.xaxis.l2p(Number(trace.x[i])) + fl._size.l;
        var py = fl.yaxis.l2p(Number(trace.y[i])) + fl._size.t;
        var dx = lx - px;
        var dy = ly - py;
        var dist = dx * dx + dy * dy;
        if (dist < bestDist) {{ bestDist = dist; best = i; }}
      }}
      // ピクセル距離で判定（ホバーは当たるがクリック判定が厳しすぎるのを防ぐ）
      if (best < 0 || bestDist > 120 * 120) return null;
      var cd = trace.customdata ? trace.customdata[best] : null;
      if (!cd) return null;
      return Array.isArray(cd) ? String(cd[0]) : String(cd);
    }} catch (e) {{
      return null;
    }}
  }}

  function bindMobileTap(gd) {{
    var tapStart = null;
    gd.addEventListener("touchstart", function(e) {{
      if (!e.touches || e.touches.length !== 1) return;
      tapStart = {{ x: e.touches[0].clientX, y: e.touches[0].clientY }};
    }}, {{ passive: true }});
    gd.addEventListener("touchend", function(e) {{
      if (gd.dataset && gd.dataset.calSwiped === "1") return;
      if (!tapStart || !e.changedTouches || e.changedTouches.length !== 1) return;
      var t = e.changedTouches[0];
      var dx = t.clientX - tapStart.x;
      var dy = t.clientY - tapStart.y;
      tapStart = null;
      if (Math.abs(dx) > 18 || Math.abs(dy) > 18) return;
      var cid = nearestCandidateFromPointer(gd, t.clientX, t.clientY);
      if (cid) emitComponentClick(cid);
    }}, {{ passive: true }});
  }}

  function bindPointerClick(gd) {{
    gd.addEventListener("click", function(e) {{
      if (gd.dataset && gd.dataset.calSwiped === "1") return;
      var cid = nearestCandidateFromPointer(gd, e.clientX, e.clientY);
      if (cid) {{
        e.preventDefault();
        e.stopPropagation();
        emitComponentClick(cid);
      }}
    }});
  }}

  var _plotInitTries = 0;
  function initPlot() {{
    _plotInitTries += 1;
    const plotEl = document.getElementById("candidate-cal-plot");
    const scrollX = document.getElementById("candidate-cal-scroll-x");
    if (!plotEl || !scrollX || typeof Plotly === "undefined") {{
      if (_plotInitTries < 40) {{
        setTimeout(initPlot, 120);
      }} else {{
        notifyCalendarReady();
      }}
      return;
    }}
    const layout = Object.assign({{}}, figLayout, {{
      height: {plot_height},
      autosize: false,
      hovermode: ("ontouchstart" in window) ? false : "closest",
      margin: Object.assign({{}}, figLayout.margin || {{}}, {{l: ML, r: MR, t: 10, b: 32}}),
    }});

    Plotly.newPlot(plotEl, figData, layout, {{
      displayModeBar: false,
      responsive: false,
      scrollZoom: false,
      doubleClick: false,
    }}).then(function(gd) {{
      syncWidth(false);
      bindHorizontalSwipe(scrollX, gd);
      bindVerticalPageScroll(document.querySelector(".candidate-cal-scroll-host"));
      bindMobileTap(gd);
      bindPointerClick(gd);
      if (window.Streamlit) {{
        Streamlit.setFrameHeight({frame_h});
        Streamlit.setComponentReady();
      }}
      notifyCalendarReady();
      gd.on("plotly_click", function(ev) {{
        if (gd.dataset && gd.dataset.calSwiped === "1") return;
        const cid = pickCandidateId(ev);
        if (cid) emitComponentClick(cid);
      }});
    }}).catch(function() {{
      if (window.Streamlit) {{
        Streamlit.setFrameHeight({frame_h});
        Streamlit.setComponentReady();
      }}
      notifyCalendarReady();
    }});

    window.addEventListener("resize", function() {{ syncWidth(true); }});
  }}

  function boot() {{
    if (window.Streamlit) {{
      Streamlit.setComponentReady();
    }}
    initPlot();
  }}

  if (document.readyState === "loading") {{
    window.addEventListener("load", boot);
  }} else {{
    boot();
  }}
}})();
</script>
""",
        height=frame_h,
        scrolling=False,
    )
    return _parse_calendar_component_value(clicked)


def _inject_calendar_scroll_setup() -> Any:
    """横スクロールとスマホ縦スクロール（タップは plotly_chart の選択で処理）。"""
    ratio = _CALENDAR_INNER_WIDTH_RATIO
    margin_l = _CALENDAR_MARGIN_LEFT
    margin_r = _CALENDAR_MARGIN_RIGHT
    return components.html(
        f"""
<script src="https://cdn.jsdelivr.net/npm/@streamlit/component-lib@2.0.0/dist/index.min.js"></script>
<script>
(function() {{
  const RATIO = {ratio};
  const ML = {margin_l};
  const MR = {margin_r};
  const doc = window.parent && window.parent.document ? window.parent.document : document;
  const PlotlyLib = (window.parent && window.parent.Plotly) || window.Plotly;

  function isMobile() {{
    try {{
      return doc.defaultView && doc.defaultView.matchMedia("(max-width: 767px)").matches;
    }} catch (e) {{
      return false;
    }}
  }}

  function applyMobileCalendarLayout(host, scrollX) {{
    if (!host || !scrollX) return;
    if (!isMobile()) {{
      host.style.maxHeight = "";
      scrollX.style.maxHeight = "";
      scrollX.style.overflowY = "visible";
      return;
    }}
    host.style.maxHeight = "52vh";
    scrollX.style.maxHeight = "calc(52vh - 52px)";
    scrollX.style.overflowY = "auto";
    scrollX.style.webkitOverflowScrolling = "touch";
  }}

  function scrollPageBy(dy) {{
    const candidates = [
      doc.querySelector('[data-testid="stAppViewContainer"]'),
      doc.querySelector("section.main"),
      doc.scrollingElement,
      doc.documentElement,
      doc.body,
    ];
    for (let i = 0; i < candidates.length; i++) {{
      const el = candidates[i];
      if (el && el.scrollHeight > el.clientHeight + 4) {{
        el.scrollTop += dy;
        return;
      }}
    }}
    if (doc.defaultView) doc.defaultView.scrollBy(0, dy);
  }}

  function clearPlotlySelection(plotDiv) {{
    if (!plotDiv || !PlotlyLib) return;
    try {{
      if (PlotlyLib.Fx && PlotlyLib.Fx.clearSelection) {{
        PlotlyLib.Fx.clearSelection(plotDiv);
      }}
    }} catch (e) {{}}
  }}

  function setPlotlyInteractive(plotDiv, enabled) {{
    if (!plotDiv) return;
    plotDiv.style.pointerEvents = enabled ? "" : "none";
  }}

  function emitGestureSuppress() {{
    try {{
      if (window.Streamlit && window.Streamlit.setComponentValue) {{
        window.Streamlit.setComponentValue(JSON.stringify({{
          event: "gesture",
          at: Date.now()
        }}));
      }}
    }} catch (e) {{}}
  }}

  function markScrollGesture(host, plotDiv) {{
    if (!host) return;
    host.dataset.calSuppressTap = "1";
    setPlotlyInteractive(plotDiv, false);
    clearPlotlySelection(plotDiv);
  }}

  function bindPlotlyGestureGuard(host, plotDiv) {{
    if (!plotDiv || plotDiv.dataset.calGestureGuard === "1") return;
    plotDiv.dataset.calGestureGuard = "1";
    const blockIfScrolling = function() {{
      if (host && host.dataset.calSuppressTap === "1") {{
        clearPlotlySelection(plotDiv);
      }}
    }};
    plotDiv.on("plotly_click", blockIfScrolling);
    plotDiv.on("plotly_selected", blockIfScrolling);
  }}

  function bindCalendarTouch(host, scrollX, plotDiv) {{
    if (!host || host.dataset.calTouchBound === "1") return;
    host.dataset.calTouchBound = "1";
    bindPlotlyGestureGuard(host, plotDiv);
    const targets = [host, scrollX, plotDiv].filter(Boolean);
    let sx = 0, sy = 0, sl = 0, mode = "";
    const onStart = function(e) {{
      if (!e.touches || e.touches.length !== 1) return;
      sx = e.touches[0].clientX;
      sy = e.touches[0].clientY;
      sl = scrollX ? scrollX.scrollLeft : 0;
      mode = "";
      host.dataset.calSuppressTap = "0";
      setPlotlyInteractive(plotDiv, true);
    }};
    const onMove = function(e) {{
      if (!e.touches || e.touches.length !== 1) return;
      const dx = e.touches[0].clientX - sx;
      const dy = e.touches[0].clientY - sy;
      if (!mode) {{
        if (Math.abs(dx) < 8 && Math.abs(dy) < 8) return;
        mode = Math.abs(dx) > Math.abs(dy) * 1.15 ? "h" : "v";
        markScrollGesture(host, plotDiv);
      }}
      if (mode === "h" && scrollX) {{
        scrollX.scrollLeft = sl - dx;
        e.preventDefault();
        e.stopPropagation();
        return;
      }}
      if (mode === "v") {{
        if (scrollX && isMobile() && scrollX.scrollHeight > scrollX.clientHeight + 4) {{
          scrollX.scrollTop -= dy;
          sy = e.touches[0].clientY;
          e.preventDefault();
          e.stopPropagation();
          return;
        }}
        scrollPageBy(-dy);
        sy = e.touches[0].clientY;
        e.preventDefault();
        e.stopPropagation();
      }}
    }};
    const onEnd = function() {{
      if (host.dataset.calSuppressTap === "1") {{
        clearPlotlySelection(plotDiv);
        emitGestureSuppress();
        setTimeout(function() {{
          host.dataset.calSuppressTap = "0";
          setPlotlyInteractive(plotDiv, true);
        }}, 420);
      }} else if (mode === "h" || mode === "v") {{
        clearPlotlySelection(plotDiv);
      }}
      mode = "";
    }};
    targets.forEach(function(t) {{
      t.addEventListener("touchstart", onStart, {{capture: true, passive: true}});
      t.addEventListener("touchmove", onMove, {{capture: true, passive: false}});
      t.addEventListener("touchend", onEnd, {{capture: true, passive: true}});
      t.addEventListener("touchcancel", onEnd, {{capture: true, passive: true}});
    }});
  }}

  function sync(host, scrollX, inner) {{
    const vp = scrollX.clientWidth;
    if (vp < 1) return;
    const full = Math.round(vp * RATIO);
    inner.style.width = full + "px";
    inner.style.minWidth = full + "px";
    inner.style.maxWidth = full + "px";
    const plotDiv = host.querySelector(".plotly-graph-div") || host.querySelector(".js-plotly-plot");
    if (plotDiv && PlotlyLib) {{
      try {{
        PlotlyLib.relayout(plotDiv, {{
          width: full,
          autosize: false,
          "margin.l": ML,
          "margin.r": MR,
        }});
      }} catch (e) {{}}
    }}
    bindCalendarTouch(host, scrollX, plotDiv);
    applyMobileCalendarLayout(host, scrollX);
  }}

  function mount() {{
    const existing = doc.querySelector(".candidate-cal-scroll-host[data-cal-ready='1']");
    if (existing) {{
      const scrollX = existing.querySelector(".candidate-cal-scroll-x");
      const inner = existing.querySelector(".candidate-cal-scroll-inner");
      if (scrollX && inner) sync(existing, scrollX, inner);
      return;
    }}
    const header = doc.querySelector(".candidate-cal-header-row");
    const plotDiv = doc.querySelector(".js-plotly-plot");
    if (!header || !plotDiv) return;
    const headerEc = header.closest('[data-testid="stElementContainer"]');
    const plotEc = plotDiv.closest('[data-testid="stElementContainer"]');
    if (!headerEc || !plotEc || headerEc.dataset.calWrapped === "1") return;

    const host = doc.createElement("div");
    host.className = "candidate-cal-scroll-host";
    host.dataset.calReady = "1";
    host.style.cssText = "width:100%;max-width:100%;overflow:hidden;box-sizing:border-box;";
    const scrollX = doc.createElement("div");
    scrollX.className = "candidate-cal-scroll-x";
    scrollX.style.cssText = "width:100%;max-width:100%;overflow-x:auto;overflow-y:visible;-webkit-overflow-scrolling:touch;overscroll-behavior-x:contain;";
    const inner = doc.createElement("div");
    inner.className = "candidate-cal-scroll-inner";
    inner.style.boxSizing = "border-box";
    scrollX.appendChild(inner);
    host.appendChild(scrollX);

    const parent = headerEc.parentElement;
    if (!parent) return;
    parent.insertBefore(host, headerEc);
    inner.appendChild(headerEc);
    inner.appendChild(plotEc);
    headerEc.dataset.calWrapped = "1";
    plotEc.dataset.calWrapped = "1";
    sync(host, scrollX, inner);
    applyMobileCalendarLayout(host, scrollX);
  }}

  mount();
  setTimeout(mount, 250);
  setTimeout(mount, 900);
  setTimeout(mount, 1800);
  if (doc.defaultView) {{
    doc.defaultView.addEventListener("resize", mount);
    doc.defaultView.addEventListener("resize", function() {{
      const host = doc.querySelector(".candidate-cal-scroll-host[data-cal-ready='1']");
      const scrollX = host && host.querySelector(".candidate-cal-scroll-x");
      if (host && scrollX) applyMobileCalendarLayout(host, scrollX);
    }});
  }}
}})();
</script>
""",
        height=0,
    )


PLOTLY_CALENDAR_KEY = "candidate_week_plot"
_CALENDAR_GESTURE_SUPPRESS_MS = 800


def _record_calendar_gesture_suppress(raw: Any) -> None:
    """components.html からのスワイプ通知で、直後の誤タップ選択を無視する."""
    if raw is None:
        return
    raw_s = str(raw).strip()
    if not raw_s.startswith("{"):
        return
    try:
        payload = json.loads(raw_s)
    except Exception:
        return
    if str(payload.get("event") or "").strip() != "gesture":
        return
    at_ms = int(payload.get("at") or 0)
    if at_ms <= 0:
        at_ms = int(datetime.now().timestamp() * 1000)
    st.session_state["_cal_gesture_suppress_until_ms"] = at_ms + _CALENDAR_GESTURE_SUPPRESS_MS


def _is_calendar_selection_suppressed() -> bool:
    until_ms = int(st.session_state.get("_cal_gesture_suppress_until_ms") or 0)
    return int(datetime.now().timestamp() * 1000) < until_ms


def _reset_plotly_calendar_widget_state() -> None:
    st.session_state.pop(PLOTLY_CALENDAR_KEY, None)
    st.session_state.pop("_cal_plotly_selection_sig", None)


def _purge_candidate_dialog_widget_keys(dcid: Optional[str] = None) -> None:
    """ダイアログ用ウィジェットキーを削除（dcid 指定時はその候補のみ、未指定は全件）。"""
    for key in list(st.session_state.keys()):
        if not isinstance(key, str):
            continue
        if not (
            key.startswith("dialog_decide_result_")
            or key.startswith("dialog_event_title_")
            or key.startswith("dialog_project_name_")
            or key.startswith("dialog_project_address_")
            or key.startswith("dialog_decide_processing_")
        ):
            continue
        if dcid is None or str(dcid) in key:
            st.session_state.pop(key, None)


def _on_candidate_dialog_close() -> None:
    """予約ダイアログ「閉じる」用 on_click（dialog 内の st.rerun は白画面の原因になる）."""
    _reset_candidate_dialog_session(clear_plotly=False)


def _render_worker_travel_popovers(
    target: Dict[str, Any],
    *,
    worker_id_to_name: Dict[str, str],
) -> None:
    """職人名タップで直前・直後の予定と住所をポップアップ表示."""
    wids = [str(x) for x in (target.get("worker_ids") or []) if str(x).strip()]
    if not wids:
        st.write("-")
        return
    adj_all = target.get("worker_adjacent_events") or {}
    st.caption("職人名をタップすると、直前・直後の予定と住所を表示します。")
    for wid in wids:
        wn = worker_id_to_name.get(wid, wid)
        adj = adj_all.get(wid) or {}
        with st.popover(f"📍 {wn}"):
            prev = adj.get("prev")
            if prev:
                st.markdown("**直前の予定**")
                st.write(f"{prev.get('summary', '（無題）')}（{prev.get('time', '')}）")
                st.write(f"**出発住所**: {prev.get('location', '住所なし')}")
            else:
                st.write("**直前の予定**: なし")
            st.divider()
            nxt = adj.get("next")
            if nxt:
                st.markdown("**直後の予定**")
                st.write(f"{nxt.get('summary', '（無題）')}（{nxt.get('time', '')}）")
                st.write(f"**向かい先**: {nxt.get('location', '住所なし')}")
            else:
                st.write("**直後の予定**: なし")
            tw = (target.get("travel_to_site_minutes_by_worker") or {}).get(wid)
            if tw is not None:
                st.caption(f"現場までの移動目安: 約{float(tw):.0f}分")


def _reset_candidate_dialog_session(*, clear_plotly: bool = False) -> None:
    """予約ダイアログを閉じる／検索し直すときの状態クリア（再検索はしない）。"""
    st.session_state.pop("candidate_dialog_id", None)
    _purge_candidate_dialog_widget_keys()
    st.session_state.pop("_cal_last_component_click", None)
    st.session_state.pop("_cal_last_component_nonce", None)
    _reset_plotly_calendar_widget_state()


# 日付列内の候補ブロック幅（x 軸データ座標。1.0 ≒ 列幅いっぱい）
_CANDIDATE_BLOCK_WIDTH = 0.9
# カレンダー表示は 1 時間刻み（検索の time_slot_minutes とは別。見やすさ優先）
_CALENDAR_DISPLAY_SLOT_MINUTES = 60


def _candidate_calendar_plot_height(day_start_hour: int, day_end_hour: int) -> int:
    """就業時間の幅に合わせたチャート高さ（設定した開始〜終了が収まるようにする）。"""
    hours = max(4, int(day_end_hour) - int(day_start_hour) + 1)
    return min(960, max(560, hours * 54 + 72))


def _collapse_candidates_for_hourly_calendar(
    candidates: List[Dict[str, Any]],
    *,
    week_start_date: date,
    visible_day_offsets: List[int],
) -> List[Dict[str, Any]]:
    """カレンダー用に「日付×開始の時」ごとに1件にまとめる（30分刻みの候補が並びすぎるのを防ぐ）."""
    week_dates = [week_start_date + timedelta(days=i) for i in range(7)]
    valid_dates = {week_dates[i].isoformat() for i in visible_day_offsets}
    best: Dict[tuple[str, int], Dict[str, Any]] = {}
    for c in candidates:
        sa: datetime = c["start_at"]
        dkey = sa.date().isoformat()
        if dkey not in valid_dates:
            continue
        key = (dkey, int(sa.hour))
        prev = best.get(key)
        if prev is None or sa < prev["start_at"]:
            best[key] = c
    return sorted(best.values(), key=lambda x: x["start_at"])


def _assign_candidate_block_lanes(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """同一日付列で時間が重なる候補を横レーンに分け、塗りつぶし＋ラベル重なりを防ぐ."""
    by_col: Dict[float, List[Dict[str, Any]]] = defaultdict(list)
    for blk in blocks:
        by_col[float(blk["xi"])].append(blk)

    placed: List[Dict[str, Any]] = []
    for _xi, col_blocks in by_col.items():
        col_blocks.sort(key=lambda b: (float(b["y0"]), float(b["y1"])))
        lane_ends: List[float] = []
        for blk in col_blocks:
            y0 = float(blk["y0"])
            y1 = float(blk["y1"])
            lane_idx = 0
            for i, end_y in enumerate(lane_ends):
                if y0 >= end_y - 0.5:
                    lane_idx = i
                    lane_ends[i] = max(lane_ends[i], y1)
                    break
            else:
                lane_idx = len(lane_ends)
                lane_ends.append(y1)
            blk["lane"] = lane_idx
        n_lanes = max(1, len(lane_ends))
        for blk in col_blocks:
            blk["n_lanes"] = n_lanes
            placed.append(blk)
    return placed


def _lane_x_bounds(xi: float, lane: int, n_lanes: int, total_width: float) -> Tuple[float, float]:
    """列内レーンごとの x0/x1（データ座標）."""
    n = max(1, n_lanes)
    lane_w = total_width / float(n)
    x0 = xi - total_width / 2.0 + lane * lane_w + lane_w * 0.06
    x1 = xi - total_width / 2.0 + (lane + 1) * lane_w - lane_w * 0.06
    return x0, x1


def _missing_prev_cache_key(
    project_id: str,
    ui_capacity: int,
    worker_ids: List[str],
    search_date: date,
) -> str:
    wkey = ",".join(sorted(str(x) for x in worker_ids))
    return f"_missing_prev_{project_id}_{ui_capacity}_{search_date.isoformat()}_{wkey}"


def _apply_plotly_point_selection(plot_state: Any, ordered_ids: List[str]) -> None:
    """Plotly のクリック選択から候補IDを取り、ダイアログ用セッションに入れる."""
    if _is_calendar_selection_suppressed():
        return
    if plot_state is None or not ordered_ids:
        return
    try:
        sel = getattr(plot_state, "selection", None)
        if sel is None and isinstance(plot_state, dict):
            sel = plot_state.get("selection")
        if sel is None:
            return
        pts = getattr(sel, "points", None)
        if pts is None and isinstance(sel, dict):
            pts = sel.get("points")
    except Exception:
        return
    if not pts:
        return
    p0 = pts[0]
    if not isinstance(p0, dict):
        return
    cid: Optional[str] = None
    cd = p0.get("customdata")
    if isinstance(cd, (list, tuple)) and len(cd) > 0:
        cid = str(cd[0])
    elif isinstance(cd, str):
        cid = cd
    point_index = p0.get("point_index")
    if not cid and point_index is not None:
        idx = int(point_index)
        if 0 <= idx < len(ordered_ids):
            cid = ordered_ids[idx]
    if not cid:
        return
    sig = f"{cid}|{point_index}"
    if sig == st.session_state.get("_cal_plotly_selection_sig"):
        return
    st.session_state["_cal_plotly_selection_sig"] = sig
    st.session_state["_cal_last_component_nonce"] = sig
    _purge_candidate_dialog_widget_keys()
    st.session_state["candidate_dialog_id"] = cid


def _build_candidate_week_plotly_figure(
    *,
    candidates: List[Dict[str, Any]],
    week_start_date: date,
    visible_day_offsets: List[int],
    slot_minutes: int,
    day_start_hour: int,
    day_end_hour: int,
    worker_id_to_name: Dict[str, str],
    vehicle_id_to_name: Dict[str, str],
    hide_xaxis_tick_labels: bool = False,
    layout_width: Optional[int] = None,
) -> tuple[go.Figure, List[str]]:
    """週間候補を Plotly で描画（開始〜終了時刻に合わせた矩形。クリックで候補IDを取得可能）.

    visible_day_offsets: 週開始日からの日オフセット（0〜6 で週7日）。
    """
    week_dates = [week_start_date + timedelta(days=i) for i in range(7)]
    valid_dates = {week_dates[i].isoformat() for i in visible_day_offsets}
    day_to_x = {off: float(local_i) for local_i, off in enumerate(visible_day_offsets)}
    n_vis = len(visible_day_offsets)

    start_minutes = day_start_hour * 60
    end_minutes = day_end_hour * 60
    total_minutes = max(1, end_minutes - start_minutes)
    display_slot = _CALENDAR_DISPLAY_SLOT_MINUTES

    candidates = _collapse_candidates_for_hourly_calendar(
        candidates,
        week_start_date=week_start_date,
        visible_day_offsets=visible_day_offsets,
    )

    candidate_blocks: List[Dict[str, Any]] = []
    hover_texts: List[str] = []
    customdata: List[str] = []
    ordered_ids: List[str] = []

    for c in candidates:
        cid = str(c.get("candidate_id", ""))
        if not cid:
            continue
        sa: datetime = c["start_at"]
        ea: datetime = c.get("end_at") or sa
        dkey = sa.date().isoformat()
        if dkey not in valid_dates:
            continue
        day_idx = (sa.date() - week_start_date).days
        if day_idx not in day_to_x:
            continue

        start_m = sa.hour * 60 + sa.minute
        hour_row_start = int(sa.hour) * 60
        y0 = max(0, hour_row_start - start_minutes)
        y1 = min(total_minutes, y0 + display_slot)
        if y1 <= y0:
            continue

        workers_text = "、".join(worker_id_to_name.get(wid, wid) for wid in c.get("worker_ids", []))
        vehicles_text = "、".join(vehicle_id_to_name.get(vid, vid) for vid in c.get("vehicle_ids", []))
        hover_lines = [
            f"候補ID: {cid}",
            f"{sa.strftime('%H:%M')}〜{ea.strftime('%H:%M')}",
            f"人数: {c.get('capacity')}",
            f"職人: {workers_text or '-'}",
            f"車両: {vehicles_text or '-'}",
        ]
        tw = c.get("travel_to_site_minutes_by_worker") or {}
        if isinstance(tw, dict) and tw:
            hover_lines.append(
                "移動(前→現場): "
                + "、".join(f"{worker_id_to_name.get(w, w)}≈{m}分" for w, m in sorted(tw.items()))
            )
        if c.get("material_completed_events_count") is not None:
            hover_lines.append(f"資材・終了済み件数: {c.get('material_completed_events_count')} 件")
        meh = c.get("material_extra_minutes")
        if meh is not None and float(meh) > 0:
            hover_lines.append(f"資材追加拘束目安: ≈{float(meh):.0f}分")
        hover_texts.append("<br>".join(hover_lines))
        customdata.append(cid)
        ordered_ids.append(cid)
        candidate_blocks.append(
            {
                "xi": day_to_x[day_idx],
                "y0": y0,
                "y1": y1,
                "label_short": sa.strftime("%H:%M"),
            }
        )

    candidate_blocks = _assign_candidate_block_lanes(candidate_blocks)

    tickvals: List[int] = []
    ticktext: List[str] = []
    slot_count = max(1, (total_minutes + display_slot - 1) // display_slot)
    for i in range(slot_count + 1):
        m_abs = start_minutes + i * display_slot
        off = i * display_slot
        if off > total_minutes:
            break
        tickvals.append(off)
        hh = m_abs // 60
        mm = m_abs % 60
        ticktext.append(f"{hh}:{mm:02d}")

    grid_line = "#d8d8d8"
    layout_shapes: List[Dict[str, Any]] = []
    for local_i, off in enumerate(visible_day_offsets):
        dcol = week_dates[off]
        layout_shapes.append(
            {
                "type": "rect",
                "xref": "x",
                "yref": "y",
                "x0": local_i - 0.5,
                "x1": local_i + 0.5,
                "y0": 0,
                "y1": total_minutes,
                "fillcolor": _column_bg_color(dcol),
                "line": {"width": 0},
                "layer": "below",
            }
        )
    for k in range(n_vis + 1):
        xv = k - 0.5
        layout_shapes.append(
            {
                "type": "line",
                "xref": "x",
                "yref": "y",
                "x0": xv,
                "x1": xv,
                "y0": 0,
                "y1": total_minutes,
                "line": {"color": grid_line, "width": 1},
                "layer": "below",
            }
        )

    annotations: List[Dict[str, Any]] = []
    hit_x: List[float] = []
    hit_y: List[float] = []
    hit_sizes: List[float] = []

    tick_headers = [_weekday_label_calendar_header(week_dates[off]) for off in visible_day_offsets]
    x_tickfont = 17 if n_vis <= 3 else (16 if n_vis <= 4 else 14)
    y_tickfont = 13
    text_px = 15 if n_vis <= 3 else (14 if n_vis <= 4 else 12)
    plot_h = _candidate_calendar_plot_height(day_start_hour, day_end_hour)

    for blk in candidate_blocks:
        xi = float(blk["xi"])
        y0 = float(blk["y0"])
        y1 = float(blk["y1"])
        lane = int(blk.get("lane", 0))
        n_lanes = int(blk.get("n_lanes", 1))
        x0, x1 = _lane_x_bounds(xi, lane, n_lanes, _CANDIDATE_BLOCK_WIDTH)
        layout_shapes.append(
            {
                "type": "rect",
                "xref": "x",
                "yref": "y",
                "x0": x0,
                "x1": x1,
                "y0": y0,
                "y1": y1,
                "fillcolor": "rgba(21, 214, 214, 0.92)",
                "line": {"color": "rgba(0, 0, 0, 0.28)", "width": 1},
                "layer": "above",
            }
        )
        block_h = y1 - y0
        x_center = (x0 + x1) / 2.0
        # 判別は開始時刻ラベル（1時間マス内の上部寄せ）
        label_px = int(min(16, max(12, text_px)))
        annotations.append(
            {
                "x": x_center,
                "y": y0 + min(14.0, block_h * 0.22),
                "text": f"<b>{blk.get('label_short') or ''}</b>",
                "showarrow": False,
                "xref": "x",
                "yref": "y",
                "xanchor": "center",
                "yanchor": "top",
                "font": {"size": label_px, "color": "#022"},
                "captureevents": False,
            }
        )
        hit_x.append(x_center)
        hit_y.append((y0 + y1) / 2.0)
        lane_w_px = (_CANDIDATE_BLOCK_WIDTH / float(max(n_lanes, 1))) / float(max(n_vis, 1)) * float(plot_h) * 0.5
        hit_sizes.append(
            float(
                max(
                    55.0,
                    min(
                        120.0,
                        (block_h / float(total_minutes)) * float(plot_h) * 0.96,
                        lane_w_px * 1.25,
                    ),
                )
            )
        )

    fig = go.Figure()
    if hit_x:
        fig.add_trace(
            go.Scatter(
                x=hit_x,
                y=hit_y,
                mode="markers",
                marker=dict(
                    size=hit_sizes,
                    color="rgba(21, 214, 214, 0.01)",
                    line=dict(width=0),
                ),
                customdata=customdata,
                hovertext=hover_texts,
                hoverinfo="text",
                name="候補",
            )
        )

    layout_kw: Dict[str, Any] = {
        "height": plot_h,
        "annotations": annotations,
        "shapes": layout_shapes,
        "margin": dict(
            l=_CALENDAR_MARGIN_LEFT,
            r=_CALENDAR_MARGIN_RIGHT,
            t=10 if hide_xaxis_tick_labels else 52,
            b=32,
        ),
        "paper_bgcolor": "#fff",
        "plot_bgcolor": "#ffffff",
        "showlegend": False,
        "dragmode": False,
        "xaxis": dict(
            side="top",
            tickmode="array",
            tickvals=list(range(n_vis)),
            ticktext=([""] * n_vis if hide_xaxis_tick_labels else tick_headers),
            showticklabels=not hide_xaxis_tick_labels,
            range=[-0.5, n_vis - 0.5],
            showgrid=False,
            zeroline=False,
            fixedrange=True,
            automargin=False,
            tickfont=dict(size=x_tickfont, color="#111"),
            showline=True,
            linecolor=grid_line,
        ),
        "yaxis": dict(
            title="",
            range=[0, total_minutes],
            autorange="reversed",
            tickmode="array",
            tickvals=tickvals,
            ticktext=ticktext,
            tickfont=dict(size=y_tickfont, color="#222"),
            showgrid=True,
            gridcolor="#c8c8c8",
            dtick=None,
            zeroline=False,
            fixedrange=True,
        ),
    }
    if layout_width is not None:
        layout_kw["width"] = int(layout_width)
        layout_kw["autosize"] = False
    else:
        layout_kw["autosize"] = True
    fig.update_layout(**layout_kw)
    return fig, ordered_ids


def _render_week_calendar(
    *,
    candidates: List[Dict[str, Any]],
    week_start_date,
    slot_minutes: int,
    day_start_hour: int,
    day_end_hour: int,
    worker_id_to_name: Dict[str, str],
    vehicle_id_to_name: Dict[str, str],
    footer_note: Optional[str] = None,
) -> None:
    """週間候補カレンダー（Plotly）。クリックで予約確定ダイアログ（st.dialog）を開く。"""
    wd: date = week_start_date
    if isinstance(wd, datetime):
        wd = wd.date()
    week_dates = [wd + timedelta(days=i) for i in range(7)]
    offsets = calendar_display_day_offsets()
    visible_dates = [week_dates[i] for i in offsets]
    d0, d1 = visible_dates[0], visible_dates[-1]
    st.caption(
        f"表示: **{d0.month}/{d0.day}（{_YOUBI[d0.weekday()]}）〜"
        f"{d1.month}/{d1.day}（{_YOUBI[d1.weekday()]}）** — "
        f"画面幅は{_CALENDAR_VIEWPORT_DAYS}日分。横スワイプで4日目以降（日曜始まり）"
    )
    st.caption(
        "**青枠をクリック／タップ**すると予約確定のポップアップ（「決定」ボタン付き）が開きます。"
        "マウスを乗せただけの吹き出しは参考表示です。"
    )
    st.markdown(
        '<p class="candidate-cal-mobile-hint">'
        "スマホ: カレンダー内を<strong>縦スワイプ</strong>で時間帯をスクロール。"
        "横スワイプで他の日付へ移動できます。"
        "</p>",
        unsafe_allow_html=True,
    )

    st.markdown(
        """
<style>
.candidate-cal-mobile-hint {
  display: none;
  margin: 0.25rem 0 0.5rem;
  font-size: 0.9rem;
  color: #444;
}
.candidate-cal-header-row {
  display: flex; width: 100%; align-items: stretch; box-sizing: border-box;
}
.candidate-cal-y-axis-gutter {
  width: 62px; min-width: 62px; flex-shrink: 0;
}
.candidate-cal-margin-right {
  width: 18px; min-width: 18px; flex-shrink: 0;
}
.candidate-cal-day-grid {
  flex: 1; display: grid; border: 1px solid #d8d8d8; border-bottom: none; box-sizing: border-box;
}
.cal-head-cell {
  box-sizing: border-box; text-align: center; padding: 10px 5px;
  font-size: 15px; color: #222; font-weight: 600;
}
.candidate-cal-scroll-host,
.candidate-cal-scroll-x {
  touch-action: pan-x pan-y !important;
}
@media (max-width: 767px) {
  .candidate-cal-mobile-hint { display: block; }
  .cal-head-cell { font-size: 14px; padding: 8px 4px; }
  .candidate-cal-scroll-host {
    max-height: 52vh;
  }
  .candidate-cal-scroll-x {
    max-height: calc(52vh - 52px);
    overflow-y: auto !important;
    -webkit-overflow-scrolling: touch;
  }
}
</style>
""",
        unsafe_allow_html=True,
    )
    st.markdown(_build_calendar_header_html(visible_dates), unsafe_allow_html=True)
    plot_h = _candidate_calendar_plot_height(day_start_hour, day_end_hour)
    fig, ordered_ids = _build_candidate_week_plotly_figure(
        candidates=candidates,
        week_start_date=wd,
        visible_day_offsets=offsets,
        slot_minutes=slot_minutes,
        day_start_hour=day_start_hour,
        day_end_hour=day_end_hour,
        worker_id_to_name=worker_id_to_name,
        vehicle_id_to_name=vehicle_id_to_name,
        hide_xaxis_tick_labels=True,
    )
    plot_state = st.plotly_chart(
        fig,
        key=PLOTLY_CALENDAR_KEY,
        on_select="rerun",
        selection_mode="points",
        use_container_width=True,
        height=plot_h,
    )
    gesture_raw = _inject_calendar_scroll_setup()
    _record_calendar_gesture_suppress(gesture_raw)
    _apply_plotly_point_selection(plot_state, ordered_ids)
    if st.session_state.get("candidate_search_display_pending"):
        _finish_candidate_search_display_if_needed()
    note = footer_note or (
        "※ 色ブロックは「空きとして採用した候補」の開始〜終了です。"
        "（上の「この週のカレンダー予定」で参照IDを確認できます）"
    )
    st.caption(note)


def render_page() -> None:
    """候補検索画面."""
    _render_candidate_search_page_body()


def _render_candidate_search_page_body() -> None:
    st.set_page_config(
        page_title=f"{APP_TITLE} - 候補検索",
        layout="wide",
        menu_items=STREAMLIT_MENU_ITEMS,
    )
    init_session_state()
    if "candidate_search_vehicle_mode" not in st.session_state:
        st.session_state["candidate_search_vehicle_mode"] = "なし"

    registered_from_dialog = st.session_state.pop(CANDIDATE_REGISTER_DIALOG_RESULT_KEY, None)
    if registered_from_dialog:
        apply_registered_project_to_candidate_search(registered_from_dialog)
        st.rerun()

    # 候補検索ページへ再入場したときは、前回候補を残さず毎回リフレッシュする（マスタはキャッシュ再利用）。
    if st.session_state.get("_active_page_id") != "candidate_search":
        st.session_state.pop("candidate_results", None)
        st.session_state.pop("candidate_search_job", None)
        st.session_state.pop("candidate_search_calendar_pending", None)
        st.session_state.pop("candidate_search_display_pending", None)
        st.session_state.pop("_last_search_calendar_bundle", None)
        st.session_state.pop("_cal_last_component_click", None)
        st.session_state.pop("_cal_last_component_nonce", None)
        _clear_candidate_search_ui_busy()
        st.session_state.pop("candidate_dialog_id", None)
        st.session_state.pop("week_nav_trigger_search", None)
    st.session_state["_active_page_id"] = "candidate_search"
    display_pending = bool(st.session_state.get("candidate_search_display_pending"))
    dialog_pending = bool(st.session_state.get("candidate_dialog_id"))
    if dialog_pending:
        st.session_state.pop("candidate_search_display_pending", None)
        display_pending = False
        _clear_candidate_search_ui_busy()
    inject_wide_layout(
        skip_busy_reset=bool(
            st.session_state.get("candidate_search_job")
            or (display_pending and not dialog_pending)
        )
    )
    inject_sidebar_nav()
    if dialog_pending or (
        not st.session_state.get("candidate_search_job") and not display_pending
    ):
        inject_clear_force_busy_overlay()

    st.title("空き日程")
    st.caption("人数と作業時間を指定して、直近の空き枠をカードで表示します。開いて確認し、そのままカレンダー登録できます。")

    notice = st.session_state.pop("schedule_commit_notice", None)
    if notice:
        st.success(notice)
    post_register_notice = st.session_state.pop("candidate_search_post_register_notice", None)
    if post_register_notice:
        st.success(post_register_notice)
    flash_warnings = list(dict.fromkeys(st.session_state.get("candidate_search_warnings_flash") or []))
    for msg in flash_warnings:
        st.warning(msg)
    # 旧実装の ?candidate_id= リンクは multipage で白画面になることがあるため廃止。残っていればクエリだけ除去して案内する。
    if "candidate_id" in st.query_params:
        try:
            del st.query_params["candidate_id"]
        except Exception:
            pass
        st.info("空きカードの「開く」から詳細を表示します。古いブックマークのクエリは無視しました。")

    week_nav_trigger = st.session_state.pop("week_nav_trigger_search", False)
    if "candidate_calendar_week_start" not in st.session_state:
        _ws_init = sunday_week_containing(date.today())
        st.session_state["candidate_calendar_week_start"] = _ws_init

    # 画面用CSS（業務向けに崩れを抑制）
    # ※ 詳細ポップアップ開閉時でも幅が変わらないよう、メインコンテナの幅を固定
    st.markdown(
        """
<style>
/* ポップアップ開閉前後でレイアウト幅が変わらないように固定 */
/* st.dialog 表示時にメインコンテンツの幅が変化するバグ対策 */
.main .block-container {
  width: 100% !important;
  max-width: 100% !important;
}
section.main > div {
  width: 100% !important;
  max-width: 100% !important;
}

/* 条件行の横並びを維持（PC向け）。スマホでは多少崩れてもよいように min-width は指定しない。 */
.nowrap-row [data-testid="stHorizontalBlock"] {
  flex-wrap: nowrap !important;
}

/* 週ナビゲーション（＜ 3月 ＞）: 候補セクションの columns を複数セレクタで指定 */
.week-nav-wrap + [data-testid="stHorizontalBlock"],
[data-testid="stMarkdown"]:has(.week-nav-wrap) + [data-testid="stHorizontalBlock"] {
  flex-wrap: wrap !important;
  width: 100% !important;
  max-width: 100% !important;
}
.week-nav-wrap + [data-testid="stHorizontalBlock"] button,
[data-testid="stMarkdown"]:has(.week-nav-wrap) + [data-testid="stHorizontalBlock"] button {
  min-width: 32px;
  height: 32px;
  padding: 0 6px;
}
/* スマホ: 3月を上に、その下に＜と＞を横並び（2行） */
@media (max-width: 767px) {
  .week-nav-wrap + [data-testid="stHorizontalBlock"],
  .week-nav-wrap + * [data-testid="stHorizontalBlock"],
  [data-testid="stMarkdown"]:has(.week-nav-wrap) + [data-testid="stHorizontalBlock"] {
    display: grid !important;
    grid-template-columns: 1fr 1fr;
    grid-template-rows: auto auto;
  }
  .week-nav-wrap + [data-testid="stHorizontalBlock"] > div:nth-child(1),
  .week-nav-wrap + * [data-testid="stHorizontalBlock"] > div:nth-child(1),
  [data-testid="stMarkdown"]:has(.week-nav-wrap) + [data-testid="stHorizontalBlock"] > div:nth-child(1) {
    grid-column: 1;
    grid-row: 2;
  }
  .week-nav-wrap + [data-testid="stHorizontalBlock"] > div:nth-child(2),
  .week-nav-wrap + * [data-testid="stHorizontalBlock"] > div:nth-child(2),
  [data-testid="stMarkdown"]:has(.week-nav-wrap) + [data-testid="stHorizontalBlock"] > div:nth-child(2) {
    grid-column: 1 / -1;
    grid-row: 1;
    text-align: center;
  }
  .week-nav-wrap + [data-testid="stHorizontalBlock"] > div:nth-child(3),
  .week-nav-wrap + * [data-testid="stHorizontalBlock"] > div:nth-child(3),
  [data-testid="stMarkdown"]:has(.week-nav-wrap) + [data-testid="stHorizontalBlock"] > div:nth-child(3) {
    grid-column: 2;
    grid-row: 2;
  }
}
/* PC: ＜ 3月 ＞ を左寄せで1行に */
@media (min-width: 768px) {
  .week-nav-wrap + [data-testid="stHorizontalBlock"],
  [data-testid="stMarkdown"]:has(.week-nav-wrap) + [data-testid="stHorizontalBlock"] {
    display: flex !important;
    flex-wrap: nowrap !important;
    justify-content: flex-start !important;
    align-items: center !important;
    gap: 8px;
  }
  .week-nav-wrap + [data-testid="stHorizontalBlock"] > div,
  [data-testid="stMarkdown"]:has(.week-nav-wrap) + [data-testid="stHorizontalBlock"] > div {
    flex: 0 0 auto !important;
    width: auto !important;
    min-width: 0 !important;
  }
}

/* ボタンの文字を改行しない（例：クリア） */
button {
  white-space: nowrap !important;
}
</style>
""",
        unsafe_allow_html=True,
    )

    # 分割検索中は毎 rerun でマスタを取り直さない（体感遅延の主因）。検索ボタン直後は on_click で先にフラグが立つ。
    cjob_early = st.session_state.get("candidate_search_job")
    cal_early = bool(st.session_state.get("candidate_search_calendar_pending"))
    search_press = bool(st.session_state.get("_candidate_search_btn_pressed"))
    st.session_state.pop("_candidate_search_btn_pressed", None)
    masters_cache = st.session_state.get("_candidate_search_masters")
    # 検索中以外の操作（新規登録フォームのチェックボックス等）でも毎回 Firestore を取り直さない
    reuse_masters = (
        isinstance(masters_cache, dict)
        and isinstance(masters_cache.get("projects"), list)
        and isinstance(masters_cache.get("workers"), list)
        and isinstance(masters_cache.get("vehicles"), list)
    )
    show_search_phase = bool(
        cjob_early
        or cal_early
        or display_pending
        or candidate_search_busy_active()
        or search_press
        or week_nav_trigger
    )
    _sanitize_stale_candidate_search_busy(starting_search=search_press or week_nav_trigger)
    if cjob_early is not None or display_pending:
        _inject_candidate_search_busy_if_needed()
    top_spinner_msg = (
        "検索・カレンダー表示中…" if show_search_phase else "データを読み込み中…"
    )

    if reuse_masters:
        projects = masters_cache["projects"]
        workers = masters_cache["workers"]
        vehicles = masters_cache["vehicles"]
    else:
        # 案件・職人・車両は Firestore（またはダミーフォールバック）から取得
        with visible_spinner(top_spinner_msg):
            try:
                _all_projects = list_projects_from_service({})
                # 対応済み（リフォーム完了）は日程候補の対象外
                projects = [p for p in _all_projects if str(p.get("status") or "") != "completed"]
            except FirestoreConnectionError:
                st.error(DB_UNAVAILABLE_MESSAGE)
                st.session_state.pop("candidate_search_display_pending", None)
                _clear_candidate_search_ui_busy()
                inject_clear_force_busy_overlay()
                return
            except Exception as exc:
                st.error("案件一覧の取得中に想定外エラーが発生しました。")
                st.exception(exc)
                st.session_state.pop("candidate_search_display_pending", None)
                _clear_candidate_search_ui_busy()
                inject_clear_force_busy_overlay()
                return

            try:
                workers = list_workers()
            except FirestoreConnectionError:
                st.error(DB_UNAVAILABLE_MESSAGE)
                st.session_state.pop("candidate_search_display_pending", None)
                _clear_candidate_search_ui_busy()
                inject_clear_force_busy_overlay()
                return
            except Exception as exc:
                st.error("職人一覧の取得中に想定外エラーが発生しました。")
                st.exception(exc)
                st.session_state.pop("candidate_search_display_pending", None)
                _clear_candidate_search_ui_busy()
                inject_clear_force_busy_overlay()
                return

            try:
                vehicles = list_vehicles()
            except FirestoreConnectionError:
                st.error(DB_UNAVAILABLE_MESSAGE)
                st.session_state.pop("candidate_search_display_pending", None)
                _clear_candidate_search_ui_busy()
                inject_clear_force_busy_overlay()
                return
            except Exception as exc:
                st.error("車両一覧の取得中に想定外エラーが発生しました。")
                st.exception(exc)
                st.session_state.pop("candidate_search_display_pending", None)
                _clear_candidate_search_ui_busy()
                inject_clear_force_busy_overlay()
                return
        st.session_state["_candidate_search_masters"] = {
            "projects": projects,
            "workers": workers,
            "vehicles": vehicles,
        }
    try:
        settings = get_settings()
    except FirestoreConnectionError:
        settings = {}

    # ----------------------------
    # 上部：検索条件（空き優先の最小項目）
    # ----------------------------
    st.subheader("条件")
    st.caption("必須は人数・作業時間・週だけです。案件を選ばなくても空きを探せます。")

    if "candidate_search_capacity" not in st.session_state:
        st.session_state["candidate_search_capacity"] = 1
    if "candidate_search_duration_minutes" not in st.session_state:
        st.session_state["candidate_search_duration_minutes"] = 120
    if "candidate_search_week_offset" not in st.session_state:
        st.session_state["candidate_search_week_offset"] = 0

    # 週オフセット → 表示週の日曜へ反映
    _week_offset = int(st.session_state.get("candidate_search_week_offset") or 0)
    st.session_state["candidate_calendar_week_start"] = _sunday_week_from_today(_week_offset)

    col_cap, col_dur, col_week = st.columns(3)
    with col_cap:
        st.number_input(
            "人数*",
            min_value=1,
            max_value=MAX_REQUIRED_WORKERS,
            step=1,
            key="candidate_search_capacity",
        )
    with col_dur:
        st.selectbox(
            "作業時間（分）*",
            options=_DURATION_OPTIONS,
            key="candidate_search_duration_minutes",
        )
    with col_week:
        week_labels = [label for _, label in _WEEK_OFFSET_OPTIONS]
        week_values = [val for val, _ in _WEEK_OFFSET_OPTIONS]
        current_offset = int(st.session_state.get("candidate_search_week_offset") or 0)
        try:
            week_index = week_values.index(current_offset)
        except ValueError:
            week_index = 0
        picked_label = st.selectbox(
            "週*",
            options=week_labels,
            index=week_index,
            key="candidate_search_week_label",
        )
        st.session_state["candidate_search_week_offset"] = week_values[week_labels.index(picked_label)]
        st.session_state["candidate_calendar_week_start"] = _sunday_week_from_today(
            int(st.session_state["candidate_search_week_offset"])
        )
        st.caption(_format_week_range_short(st.session_state["candidate_calendar_week_start"]))

    b1, b2, _bpad = st.columns([1.2, 1.2, 3.6])
    with b1:
        clear_clicked = st.button("クリア", use_container_width=True)
    with b2:
        search_clicked = st.button(
            "空きを探す",
            type="primary",
            use_container_width=True,
            on_click=_on_candidate_search_button_click,
        )

    with st.expander("詳細条件（案件・職人・車両）", expanded=False):
        st.caption(
            "必要なら案件紐付けや職人指定を使えます。"
            " 対応済み案件は候補対象外です。"
        )
        render_candidate_search_register_ui()
        cal_col, _ = st.columns([1, 3])
        with cal_col:
            if st.button("カレンダー情報収集", key="candidate_search_calendar_collect_btn"):
                navigate_to_project_list_calendar_collect()

        project_options = {p["project_name"]: p for p in projects}
        project_name_list = list(project_options.keys())

        selected_project_name = render_searchable_selectbox(
            "案件（任意）",
            project_name_list,
            select_key="candidate_search_project_select",
            query_key="candidate_search_project_query",
            placeholder="案件名を入力して絞り込み・選択…",
            help="選ばなくても空き検索できます。選ぶと住所・移動判定に使います。",
        )
        if search_press:
            _resolved_on_search = resolve_combo_selection(
                "candidate_search_project_select",
                project_name_list,
                query_key="candidate_search_project_query",
            )
            if _resolved_on_search:
                selected_project_name = _resolved_on_search
        selected_project = project_options.get(selected_project_name)

        vehicle_mode = st.radio(
            "車両",
            options=["なし", "あり"],
            horizontal=True,
            key="candidate_search_vehicle_mode",
            help="なし: 職人の Google カレンダーのみで候補を出します。あり: 従来どおり車両の空きも確認します。",
        )
        use_vehicle_calendar = vehicle_mode == "あり"
        if not use_vehicle_calendar:
            st.caption(
                "車両の Google カレンダーは使いません。候補確定時も職人カレンダーのみ登録されます。"
            )

        # 案件を変えたときは人数を案件の必要人数に揃える（詳細利用時のみ）
        _prev_proj_key = st.session_state.get("_candidate_sync_project_key")
        _cur_proj_key = selected_project_name or ""
        if _cur_proj_key != _prev_proj_key:
            st.session_state["_candidate_sync_project_key"] = _cur_proj_key
            if selected_project:
                try:
                    rw = int(selected_project.get("required_workers") or 0)
                    if rw > 0:
                        st.session_state["candidate_search_capacity"] = max(1, rw)
                except (TypeError, ValueError):
                    pass
                try:
                    dur = int(selected_project.get("work_duration_minutes") or 0)
                    if dur in _DURATION_OPTIONS:
                        st.session_state["candidate_search_duration_minutes"] = dur
                except (TypeError, ValueError):
                    pass

        worker_options: List[Dict[str, str]] = []
        for w in workers:
            wid = str(w.get("worker_id") or "")
            wname = str(w.get("name") or wid)
            wrank = str(w.get("rank") or "").strip()
            rank_label = wrank if wrank else "ランク未設定"
            worker_options.append(
                {
                    "value": wid,
                    "label": f"{wname} [{rank_label}]",
                }
            )
        worker_value_to_label = {o["value"]: o["label"] for o in worker_options}
        worker_values = [o["value"] for o in worker_options]
        rank_options_raw = settings.get("worker_ranks") or []
        rank_options = (
            [str(x).strip() for x in rank_options_raw if str(x).strip()]
            if isinstance(rank_options_raw, list)
            else []
        )

        w1, w2, w3 = st.columns([3.0, 1.2, 2.2])
        with w1:
            selected_worker_ids = st.multiselect(
                "職人",
                options=worker_values,
                default=st.session_state.get("worker_multi_select", []),
                key="worker_multi_select",
                format_func=lambda v: worker_value_to_label.get(v, v),
                placeholder="（指定なし）",
            )
        with w2:
            include_mode = st.selectbox(
                "条件",
                options=["含む", "含まない"],
                key="worker_include_mode",
            )
        with w3:
            st.multiselect(
                "ランク絞り込み",
                options=rank_options,
                default=st.session_state.get("worker_rank_filters", []),
                key="worker_rank_filters",
                placeholder="ランク絞り込み（複数選択）",
            )

    # expander 外でも参照できるよう既定値を用意
    project_options = {p["project_name"]: p for p in projects}
    project_name_list = list(project_options.keys())
    if "selected_project_name" not in locals():
        selected_project_name = resolve_combo_selection(
            "candidate_search_project_select",
            project_name_list,
            query_key="candidate_search_project_query",
        )
        selected_project = project_options.get(selected_project_name)
    if "use_vehicle_calendar" not in locals():
        use_vehicle_calendar = st.session_state.get("candidate_search_vehicle_mode", "なし") == "あり"
    if "include_mode" not in locals():
        include_mode = str(st.session_state.get("worker_include_mode") or "含む")

    required_capacity = int(st.session_state.get("candidate_search_capacity", 1) or 1)
    work_duration_minutes = int(
        st.session_state.get("candidate_search_duration_minutes", 120) or 120
    )
    loc_ov: Dict[str, str] = st.session_state.setdefault("candidate_location_overrides", {})

    def _workers_filtered_by_rank(src: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        selected_workers = {
            str(x).strip()
            for x in (st.session_state.get("worker_multi_select") or [])
            if str(x).strip()
        }
        selected = {
            str(x).strip()
            for x in (st.session_state.get("worker_rank_filters") or [])
            if str(x).strip()
        }
        rows = list(src)
        if selected_workers:
            rows = [w for w in rows if str(w.get("worker_id") or "").strip() in selected_workers]
        if selected:
            rows = [w for w in rows if str(w.get("rank") or "").strip() in selected]
        return rows

    workers_for_search = _workers_filtered_by_rank(workers)
    include_mode = str(st.session_state.get("worker_include_mode") or "含む")

    # 分割検索: ①カレンダーAPIは表示中の4日/3日分のみ ②以降は同一データで1日ずつ計算
    cjob = st.session_state.get("candidate_search_job")
    if cjob is not None or st.session_state.get("candidate_search_display_pending"):
        _inject_candidate_search_busy_if_needed()
    if cjob is not None:
        _btn_search = bool(cjob.get("from_search_btn"))
        step = int(cjob.get("step", -99))
        ws_job = cjob["week_start"]
        excl_job = {str(x) for x in cjob.get("excluded", [])}
        must_inc_job = [str(x) for x in cjob.get("must_include", [])]
        pj_n = (cjob.get("project_name") or "").strip()
        selected_for_job = project_options.get(pj_n) if pj_n else None
        cap_job = int(cjob.get("required_capacity", 0))
        dur_job = int(cjob.get("work_duration_minutes") or 120)
        proj_job = _build_search_project(
            selected_for_job,
            required_capacity=cap_job,
            work_duration_minutes=dur_job,
        )
        use_vc_job = bool(cjob.get("use_vehicle_calendar", False))
        try:
            settings_job = get_settings()
        except FirestoreConnectionError:
            settings_job = {}
        gcal_tok = st.session_state.get("google_calendar_tokens") or {}
        vf_sess = gcal_tok.get("vehicle_fleet") if isinstance(gcal_tok, dict) else None

        day_offsets_job: List[int] = list(cjob.get("day_offsets") or calendar_display_day_offsets())
        n_search_days = len(day_offsets_job)
        job_started = cjob.get("search_started_at")
        if isinstance(job_started, str):
            try:
                job_started_dt = datetime.fromisoformat(job_started)
                if job_started_dt.tzinfo is None:
                    job_started_dt = job_started_dt.replace(tzinfo=ZoneInfo("Asia/Tokyo"))
            except Exception:
                job_started_dt = datetime.now(ZoneInfo("Asia/Tokyo"))
        elif isinstance(job_started, datetime):
            job_started_dt = job_started
        else:
            job_started_dt = datetime.now(ZoneInfo("Asia/Tokyo"))

        if step == -1:
            with visible_spinner("カレンダー取得中…"):
                bundle, wpre = fetch_week_calendar_events_bundle(
                    project=proj_job,
                    workers=workers_for_search,
                    vehicles=vehicles,
                    settings=settings_job,
                    ui_capacity=cap_job,
                    session_tokens=st.session_state.get("google_calendar_tokens"),
                    vehicle_fleet_session=vf_sess,
                    excluded_worker_ids=excl_job,
                    search_week_start=ws_job,
                    search_day_offsets=day_offsets_job,
                    use_vehicle_calendar=use_vc_job,
                )
            if wpre:
                cjob["warnings_acc"].extend(wpre)
            if bundle is None:
                st.session_state["candidate_search_warnings_flash"] = list(
                    dict.fromkeys(cjob.get("warnings_acc") or [])
                )
                st.session_state.pop("candidate_search_job", None)
                st.session_state.pop("candidate_search_calendar_pending", None)
                st.session_state.pop("candidate_search_display_pending", None)
                _clear_candidate_search_ui_busy()
                inject_clear_force_busy_overlay()
                st.rerun()
            cjob["bundle"] = bundle
            cjob["step"] = 0
            st.rerun()
        elif step < n_search_days:
            d = ws_job + timedelta(days=day_offsets_job[step])
            with visible_spinner(
                f"検索中…（{format_search_progress_pct(step, n_search_days)}）"
            ):
                part, warns = search_candidates(
                    project=proj_job,
                    workers=workers_for_search,
                    vehicles=vehicles,
                    settings=settings_job,
                    ui_capacity=cap_job,
                    session_tokens=st.session_state.get("google_calendar_tokens"),
                    vehicle_fleet_session=vf_sess,
                    location_overrides=st.session_state.get("candidate_location_overrides") or {},
                    excluded_worker_ids=excl_job,
                    must_include_worker_ids=must_inc_job,
                    search_week_start=ws_job,
                    limit_search_days=[d],
                    shared_events_by_calendar_id=cjob["bundle"],
                    use_vehicle_calendar=use_vc_job,
                    search_started_at=job_started_dt,
                )
            cjob["accum"].extend(part)
            cjob["warnings_acc"].extend(warns)
            cjob["step"] = step + 1
            st.rerun()
        else:
            st.session_state["candidate_results"] = cjob["accum"]
            st.session_state["candidate_search_warnings_flash"] = list(
                dict.fromkeys(cjob.get("warnings_acc") or [])
            )
            bundle_done = cjob.get("bundle")
            if bundle_done:
                st.session_state["_last_search_calendar_bundle"] = {
                    "week_start": ws_job.isoformat(),
                    "use_vehicle_calendar": use_vc_job,
                    "bundle": bundle_done,
                }
            st.session_state.pop("candidate_search_calendar_pending", None)
            st.session_state.pop("candidate_search_job", None)
            st.session_state.pop("_week_nav_undo", None)
            st.session_state.pop("candidate_search_display_pending", None)
            _reset_candidate_dialog_session(clear_plotly=True)
            _clear_candidate_search_ui_busy()
            st.rerun()

    if selected_project:
        _mp_wids = sorted(str(w.get("worker_id", "")) for w in workers_for_search)
        _mp_key = _missing_prev_cache_key(
            str(selected_project.get("project_id", "")),
            required_capacity,
            _mp_wids,
            date.today(),
        )
        if _mp_key not in st.session_state:
            with visible_spinner("前現場情報を確認中…"):
                st.session_state[_mp_key] = collect_missing_previous_locations(
                    project=selected_project,
                    workers=workers_for_search,
                    ui_capacity=required_capacity,
                    session_tokens=st.session_state.get("google_calendar_tokens"),
                    location_overrides=loc_ov,
                    search_date=date.today(),
                )
        missing_prev = st.session_state.get(_mp_key) or []
        if missing_prev:
            with st.expander("前現場の住所がカレンダーにない予定（暫定住所）", expanded=False):
                st.caption("同じ予定を共有している職人には、一括で住所を反映できます。")
                by_eid: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
                for m in missing_prev:
                    by_eid[str(m["event_id"])].append(m)
                for eid, rows in by_eid.items():
                    summ = (rows[0].get("event_summary") or "")[:40] or eid
                    when_text = str(rows[0].get("event_when_text") or "").strip()
                    if not when_text:
                        es = rows[0].get("event_start_at")
                        ee = rows[0].get("event_end_at")
                        if isinstance(es, datetime) and isinstance(ee, datetime):
                            when_text = f"{es.strftime('%m/%d %H:%M')}〜{ee.strftime('%H:%M')}"
                    when = f"（{when_text}）" if when_text else ""
                    if len(rows) > 1:
                        names = "、".join(str(r.get("worker_name", "")) for r in rows)
                        st.write(f"**{names}** さんの前予定「{summ}」{when} に住所がありません。")
                        batch_key = f"batch_addr_{eid}"
                        addr_in = st.text_input(
                            "暫定住所（一括反映）",
                            key=batch_key,
                            placeholder="例: 東京都杉並区…",
                        )
                        if st.button("一括で反映", key=f"apply_batch_{eid}"):
                            addr_val = (addr_in or "").strip()
                            for r in rows:
                                loc_ov[str(r["override_key"])] = addr_val
                            errs = apply_previous_location_overrides_to_calendars(
                                workers=workers,
                                session_tokens=st.session_state.get("google_calendar_tokens"),
                                updates=[
                                    (
                                        str(r.get("worker_id", "")),
                                        str(r.get("event_id", "")),
                                        addr_val,
                                    )
                                    for r in rows
                                    if addr_val
                                ],
                            )
                            if errs:
                                st.session_state["candidate_search_warnings_flash"] = list(
                                    dict.fromkeys(
                                        (st.session_state.get("candidate_search_warnings_flash") or [])
                                        + errs
                                    )
                                )
                            st.session_state["week_nav_trigger_search"] = True
                            st.rerun()
                    else:
                        r = rows[0]
                        st.write(
                            f"**{r.get('worker_name', '')}** さんの前予定「{summ}」{when} に住所がありません。"
                        )
                        sk = f"single_addr_{r['override_key']}"
                        addr_one = st.text_input(
                            "暫定住所",
                            key=sk,
                            placeholder="例: 東京都杉並区…",
                        )
                        if st.button("反映", key=f"apply_single_{r['override_key']}"):
                            addr_val = (addr_one or "").strip()
                            loc_ov[str(r["override_key"])] = addr_val
                            errs = apply_previous_location_overrides_to_calendars(
                                workers=workers,
                                session_tokens=st.session_state.get("google_calendar_tokens"),
                                updates=[
                                    (
                                        str(r.get("worker_id", "")),
                                        str(r.get("event_id", "")),
                                        addr_val,
                                    )
                                ]
                                if addr_val
                                else [],
                            )
                            if errs:
                                st.session_state["candidate_search_warnings_flash"] = list(
                                    dict.fromkeys(
                                        (st.session_state.get("candidate_search_warnings_flash") or [])
                                        + errs
                                    )
                                )
                            st.session_state["week_nav_trigger_search"] = True
                            st.rerun()

    if clear_clicked:
        for k in (
            "candidate_search_project_select",
            "candidate_search_project_draft",
            "candidate_search_project_query",
            "_candidate_sync_project_key",
            "worker_multi_select",
            "worker_include_mode",
            "worker_rank_filters",
            "candidate_search_capacity",
            "candidate_search_duration_minutes",
            "candidate_search_week_offset",
            "candidate_search_week_label",
            "candidate_location_overrides",
        ):
            if k in st.session_state:
                del st.session_state[k]
        if "candidate_results" in st.session_state:
            del st.session_state["candidate_results"]
        st.session_state.pop("candidate_search_job", None)
        st.session_state.pop("candidate_search_calendar_pending", None)
        st.session_state.pop("candidate_search_display_pending", None)
        st.session_state.pop("_cal_last_component_click", None)
        st.session_state.pop("_cal_last_component_nonce", None)
        _clear_candidate_search_ui_busy()
        inject_clear_force_busy_overlay()
        st.session_state.pop("_candidate_search_masters", None)
        st.session_state["candidate_search_capacity"] = 1
        st.session_state["candidate_search_duration_minutes"] = 120
        st.session_state["candidate_search_week_offset"] = 0
        st.session_state["candidate_calendar_week_start"] = sunday_week_containing(date.today())
        st.rerun()

    # 案件情報（画像では条件の下に説明/詳細があるが、ここでは必要情報のみ簡潔に表示）
    if selected_project:
        with st.expander("案件情報", expanded=False):
            col_left, col_right = st.columns(2)
            with col_left:
                st.write(f"**案件名**：{selected_project['project_name']}")
                st.write(f"**顧客名**：{selected_project['customer_name']}")
                st.write(f"**住所**：{selected_project['address']}")
            with col_right:
                st.write(f"**作業時間（分）**：{selected_project['work_duration_minutes']}")
                st.write(f"**必要人数**：{selected_project['required_workers']}")
                st.write(f"**必要車両数**：{selected_project['required_vehicle_count']}")
                st.write(f"**備考**：{selected_project.get('note') or '-'}")
            _ss = selected_project.get("scheduled_start_at")
            _se = selected_project.get("scheduled_end_at")
            _refs = selected_project.get("google_calendar_event_refs") or []
            _has_refs = isinstance(_refs, list) and len(_refs) > 0
            if _ss or _se:
                try:
                    tz = ZoneInfo("Asia/Tokyo")
                    _left = (
                        datetime.fromisoformat(str(_ss).replace("Z", "+00:00")).astimezone(tz).strftime(
                            "%Y-%m-%d %H:%M"
                        )
                        if _ss
                        else "—"
                    )
                    _right = (
                        datetime.fromisoformat(str(_se).replace("Z", "+00:00")).astimezone(tz).strftime(
                            "%Y-%m-%d %H:%M"
                        )
                        if _se
                        else "—"
                    )
                except (TypeError, ValueError):
                    _left, _right = str(_ss or "—"), str(_se or "—")
                st.write(f"**予定日時（確定済み）**：{_left} 〜 {_right}")
            elif _has_refs:
                st.caption("Google カレンダーへの参照のみ保存されています。下のボタンで削除できます。")
            if bool(str(_ss or "").strip()) or bool(str(_se or "").strip()) or _has_refs:
                if st.button(
                    "予定を取り消す（Googleカレンダー削除＋案件の予定日時もクリア）",
                    key="candidate_clear_schedule_btn",
                ):
                    try:
                        settings_for_clear = get_settings()
                    except FirestoreConnectionError:
                        settings_for_clear = {}
                    gcal_tok = st.session_state.get("google_calendar_tokens") or {}
                    vf_sess = gcal_tok.get("vehicle_fleet") if isinstance(gcal_tok, dict) else None
                    try:
                        msgs_clear, ok_clear = remove_project_schedule_from_google(
                            project=selected_project,
                            workers=workers,
                            vehicles=vehicles,
                            session_tokens=st.session_state.get("google_calendar_tokens"),
                            settings=settings_for_clear,
                            vehicle_fleet_session=vf_sess,
                            current_user_name=st.session_state.get("current_user_name"),
                        )
                    except Exception as exc:
                        st.error("予定の取り消し中にエラーが発生しました。")
                        st.exception(exc)
                    else:
                        for m in msgs_clear:
                            st.info(m)
                        if ok_clear:
                            st.session_state["schedule_commit_notice"] = (
                                "Google カレンダーの予定を削除し、案件の予定日時をクリアしました。"
                            )
                            st.rerun()

    week_calendar_browse = bool(st.session_state.pop("week_calendar_browse", False))
    browse_only_view = week_calendar_browse

    # 週移動での再実行時にもカレンダーを維持（来週・再来週の予定閲覧も含む）
    if (
        not search_clicked
        and not search_press
        and not week_nav_trigger
        and not week_calendar_browse
        and "candidate_results" not in st.session_state
        and not st.session_state.get("candidate_search_job")
    ):
        # 検索結果がないのに候補だけ開こうとした（URL直打ち等）
        if st.session_state.get("candidate_dialog_id"):
            st.warning("候補を表示するには、先に検索を実行してください。")
            st.session_state.pop("candidate_dialog_id", None)
        # 検索実行前はカレンダー枠だけ表示しない（画像に近い挙動）
        return

    # 人数・作業時間があれば案件未選択でも候補表示する
    if search_clicked or search_press:
        _final_name = resolve_combo_selection(
            "candidate_search_project_select",
            project_name_list,
            query_key="candidate_search_project_query",
        )
        if _final_name:
            selected_project_name = _final_name
            selected_project = project_options.get(_final_name)
        required_capacity = int(st.session_state.get("candidate_search_capacity", 1) or 1)
        work_duration_minutes = int(
            st.session_state.get("candidate_search_duration_minutes", 120) or 120
        )

    if required_capacity <= 0 and (search_clicked or search_press):
        st.error("人数を1人以上指定してください。")
        _clear_candidate_search_ui_busy()
        inject_clear_force_busy_overlay()
        return

    if week_nav_trigger and required_capacity <= 0:
        prev_ws = st.session_state.pop("_week_nav_undo", None)
        if prev_ws is not None:
            st.session_state["candidate_calendar_week_start"] = prev_ws
        st.error("週を移動して再検索するには、人数を指定してください。")
        _clear_candidate_search_ui_busy()
        inject_clear_force_busy_overlay()
        return

    try:
        # 検索ボタン／週ナビ → カレンダー1回取得＋表示チャンク日数分の分割計算（candidate_search_job ブロック）
        run_search = search_clicked or search_press or week_nav_trigger
        if run_search:
            st.session_state["candidate_search_ui_busy"] = True
            st.session_state.pop("candidate_results", None)
            _reset_candidate_dialog_session(clear_plotly=True)
            st.session_state.pop("candidate_search_warnings_flash", None)
            for _mk in list(st.session_state.keys()):
                if isinstance(_mk, str) and _mk.startswith("_missing_prev_"):
                    del st.session_state[_mk]
            st.session_state.pop("_last_search_calendar_bundle", None)
            ws_target = st.session_state["candidate_calendar_week_start"]
            now_jst = datetime.now(ZoneInfo("Asia/Tokyo"))
            end_hhmm = str(settings.get("work_hours_end") or "19:00").strip()
            try:
                _eh, _em = end_hhmm.split(":", 1)
                week_end_cutoff = datetime.combine(
                    ws_target + timedelta(days=6),
                    time(hour=int(_eh), minute=int(_em)),
                    tzinfo=ZoneInfo("Asia/Tokyo"),
                )
            except Exception:
                week_end_cutoff = datetime.combine(
                    ws_target + timedelta(days=6),
                    time(hour=19, minute=0),
                    tzinfo=ZoneInfo("Asia/Tokyo"),
                )
            if week_end_cutoff <= now_jst:
                ws_target = ws_target + timedelta(days=7)
                st.session_state["candidate_calendar_week_start"] = ws_target
                # 週セレクトも翌週側へ寄せる
                try:
                    base = sunday_week_containing(date.today())
                    offset = max(0, (ws_target - base).days // 7)
                    st.session_state["candidate_search_week_offset"] = min(offset, 3)
                except Exception:
                    pass
                st.info("表示週が過去枠のみのため、翌週に切り替えて検索します。")
            selected_ids_set = {
                str(x).strip()
                for x in (st.session_state.get("worker_multi_select") or [])
                if str(x).strip()
            }
            excluded_for_real: set = set()
            must_include_worker_ids: List[str] = []
            if include_mode == "含まない" and selected_ids_set:
                excluded_for_real = selected_ids_set
            elif include_mode == "含む" and selected_ids_set:
                # headcount=1 の既存仕様（優先フォールバック）を維持するため must_include に渡す
                must_include_worker_ids = sorted(selected_ids_set)

            day_offsets = calendar_display_day_offsets()
            st.session_state.pop("_candidate_search_btn_pressed", None)
            st.session_state["candidate_search_job"] = {
                "step": -1,
                "accum": [],
                "warnings_acc": [],
                "week_start": ws_target,
                "day_offsets": day_offsets,
                "search_started_at": datetime.now(ZoneInfo("Asia/Tokyo")).isoformat(),
                "project_name": selected_project_name or "",
                "required_capacity": required_capacity,
                "work_duration_minutes": work_duration_minutes,
                "excluded": list(excluded_for_real),
                "must_include": list(must_include_worker_ids),
                "from_search_btn": bool(search_clicked or week_nav_trigger),
                "use_vehicle_calendar": use_vehicle_calendar,
            }
            st.rerun()
        else:
            filtered = list(st.session_state.get("candidate_results") or [])
    except Exception as exc:
        # 想定外エラー
        st.error("候補検索中に想定外エラーが発生しました。")
        st.exception(exc)
        st.session_state.pop("candidate_search_job", None)
        st.session_state.pop("candidate_search_calendar_pending", None)
        st.session_state.pop("candidate_search_display_pending", None)
        _clear_candidate_search_ui_busy()
        inject_clear_force_busy_overlay()
        return

    worker_id_to_name = {w["worker_id"]: w["name"] for w in workers}
    vehicle_id_to_name = {v["vehicle_id"]: v["name"] for v in vehicles}

    try:
        cal_settings = get_settings()
    except FirestoreConnectionError:
        cal_settings = {}
    workers_by_id = {str(w["worker_id"]): w for w in workers}
    display_candidates = [
        c
        for c in (filtered or [])
        if isinstance(c.get("start_at"), datetime)
        and not is_company_closed_day(c["start_at"].date(), cal_settings)
        and not candidate_includes_worker_off(c, workers_by_id)
    ]
    display_candidates = sorted(
        display_candidates,
        key=lambda c: c.get("start_at") or datetime.max.replace(tzinfo=ZoneInfo("Asia/Tokyo")),
    )

    st.subheader("空き枠")
    if not display_candidates:
        if browse_only_view or not _has_candidate_search_results():
            st.caption("上の「空きを探す」を押すと、条件に合う直近の空き枠がカードで表示されます。")
        else:
            st.info(
                "空き枠が見つかりませんでした。人数・作業時間・週、職人連携、就業時間、カレンダー上の空きを確認してください。"
            )
    else:
        _render_free_slot_cards(
            display_candidates,
            worker_id_to_name=worker_id_to_name,
        )

    ws = st.session_state["candidate_calendar_week_start"]
    with st.expander("週を切り替えて再検索 / 週カレンダー（任意）", expanded=False):
        st.caption("必要なら表示週を変えて再検索できます。")
        q1, q2, q3, q4 = st.columns(4)
        with q1:
            if st.button("今週", key="week_jump_0", use_container_width=True):
                st.session_state["candidate_search_week_offset"] = 0
                _go_to_calendar_week(_sunday_week_from_today(0), trigger_research=True)
        with q2:
            if st.button("来週", key="week_jump_1", use_container_width=True):
                st.session_state["candidate_search_week_offset"] = 1
                _go_to_calendar_week(_sunday_week_from_today(1), trigger_research=True)
        with q3:
            if st.button("再来週", key="week_jump_2", use_container_width=True):
                st.session_state["candidate_search_week_offset"] = 2
                _go_to_calendar_week(_sunday_week_from_today(2), trigger_research=True)
        with q4:
            if st.button("翌々週", key="week_jump_3", use_container_width=True):
                st.session_state["candidate_search_week_offset"] = 3
                _go_to_calendar_week(_sunday_week_from_today(3), trigger_research=True)

        st.markdown('<div class="week-nav-wrap">', unsafe_allow_html=True)
        col_prev, col_month, col_next = st.columns([1.0, 2.0, 1.0])
        with col_prev:
            if st.button("＜", key="week_prev_btn"):
                _go_to_calendar_week(ws - timedelta(days=7), trigger_research=True)
        with col_month:
            st.markdown(
                f"<div style='text-align:left;font-weight:700;'>{_format_week_range_short(ws)}</div>",
                unsafe_allow_html=True,
            )
        with col_next:
            if st.button("＞", key="week_next_btn"):
                _go_to_calendar_week(ws + timedelta(days=7), trigger_research=True)
        st.markdown("</div>", unsafe_allow_html=True)

        try:
            slot_gran = int(cal_settings.get("time_slot_minutes") or 30)
        except (TypeError, ValueError):
            slot_gran = 30
        dsh, deh = work_hours_display_hours(cal_settings)
        _render_week_calendar(
            candidates=display_candidates,
            week_start_date=st.session_state["candidate_calendar_week_start"],
            slot_minutes=slot_gran,
            day_start_hour=dsh,
            day_end_hour=deh,
            worker_id_to_name=worker_id_to_name,
            vehicle_id_to_name=vehicle_id_to_name,
            footer_note="※ 補助表示です。通常は上の空きカードから開いてください。",
        )

    dcid = st.session_state.get("candidate_dialog_id")
    tap_nonce = (
        st.session_state.get("_cal_last_component_nonce")
        or st.session_state.get("_cal_plotly_selection_sig")
    )
    if dcid and filtered and tap_nonce:
        if st.session_state.get("candidate_search_display_pending"):
            _finish_candidate_search_display_if_needed()
        else:
            _clear_candidate_search_ui_busy()
            inject_clear_force_busy_overlay()
        dcid_s = str(dcid)
        target = next(
            (c for c in filtered if str(c.get("candidate_id")) == dcid_s),
            None,
        )
        if target is None:
            st.session_state.pop("candidate_dialog_id", None)
            st.session_state.pop("_cal_last_component_click", None)
            st.session_state.pop("_cal_last_component_nonce", None)
        else:
            start_at_d: datetime = target["start_at"]
            end_at_d: datetime = target.get("end_at") or start_at_d
            workers_text_d = "、".join(
                worker_id_to_name.get(wid, wid) for wid in target.get("worker_ids", [])
            )
            vehicles_text_d = "、".join(
                vehicle_id_to_name.get(vid, vid) for vid in target.get("vehicle_ids", [])
            )

            @st.dialog("予約確定")
            def _show_candidate_detail() -> None:
                wid = f"{dcid}_{tap_nonce}"
                result_key = f"dialog_decide_result_{wid}"
                st.write(f"**日時**: {_format_slot_card_label(start_at_d, end_at_d)}")
                st.write(f"**対応可能人数**: {target.get('capacity')} 人")
                st.write("**職人**")
                if target.get("worker_adjacent_events"):
                    _render_worker_travel_popovers(
                        target,
                        worker_id_to_name=worker_id_to_name,
                    )
                else:
                    st.write(workers_text_d or "-")
                    st.caption(
                        "直前・直後の住所は再検索後の候補から表示できます。"
                    )
                st.write(f"**車両**: {vehicles_text_d or '-'}")
                tw_d = target.get("travel_to_site_minutes_by_worker") or {}
                if isinstance(tw_d, dict) and tw_d and not target.get("worker_adjacent_events"):
                    tw_parts = []
                    for wid_m, minutes in sorted(tw_d.items()):
                        wn = worker_id_to_name.get(wid_m, wid_m)
                        tw_parts.append(f"{wn} 約{minutes}分")
                    st.write("**移動（前現場→現場）**: " + "、".join(tw_parts))
                if target.get("travel_to_site_minutes_max") is not None:
                    st.caption(
                        f"最大移動時間の目安: 約{float(target['travel_to_site_minutes_max']):.0f}分"
                    )
                if target.get("material_completed_events_count") is not None:
                    st.write(
                        f"**資材（当日終了済み件数・代表車両）**: "
                        f"{target.get('material_completed_events_count')} 件"
                    )
                mex = target.get("material_extra_minutes")
                if mex is not None and float(mex) > 0:
                    st.caption(f"資材ルールによる追加拘束の目安: 約{float(mex):.0f}分")
                processing_key = f"dialog_decide_processing_{wid}"
                processing = bool(st.session_state.get(processing_key, False))
                project_name_key = f"dialog_project_name_{wid}"
                project_address_key = f"dialog_project_address_{wid}"
                if not selected_project:
                    st.text_input(
                        "案件名*",
                        key=project_name_key,
                        placeholder="例: ○○様 ガラス交換",
                        disabled=processing,
                    )
                    st.text_input(
                        "現場住所（任意）",
                        key=project_address_key,
                        placeholder="後から案件一覧で編集もできます",
                        disabled=processing,
                    )
                    st.caption("決定すると案件を簡易作成し、Googleカレンダーへ登録します。")
                decide_result = st.session_state.get(result_key)
                if processing:
                    st.info("処理中です。しばらくお待ちください…")
                elif decide_result == "success":
                    st.success("カレンダー登録が完了しました。内容を確認して「閉じる」を押してください。")
                elif decide_result == "partial":
                    st.warning("一部の登録に失敗しました。内容を確認して「閉じる」を押してください。")
                elif decide_result == "failed":
                    st.error("登録に失敗しました。内容を確認して「閉じる」を押してください。")
                col_close, col_decide = st.columns(2)
                with col_close:
                    st.button(
                        "閉じる",
                        key=f"dialog_close_{wid}",
                        disabled=processing,
                        on_click=_on_candidate_dialog_close,
                    )
                with col_decide:
                    if st.button(
                        "カレンダーに入れる",
                        type="primary",
                        key=f"dialog_decide_{wid}",
                        disabled=processing or decide_result in ("success", "partial"),
                    ):
                        st.session_state[processing_key] = True
                        st.rerun()

                if processing:
                    project_for_commit = selected_project
                    custom_title = ""
                    if not selected_project:
                        project_name_input = str(st.session_state.get(project_name_key) or "").strip()
                        if not project_name_input:
                            st.error("案件名を入力してから「カレンダーに入れる」を押してください。")
                            st.session_state.pop(processing_key, None)
                            return
                        address_input = str(st.session_state.get(project_address_key) or "").strip()
                        try:
                            project_for_commit = create_quick_project(
                                project_name=project_name_input,
                                required_workers=int(
                                    target.get("capacity")
                                    or st.session_state.get("candidate_search_capacity")
                                    or 1
                                ),
                                work_duration_minutes=int(
                                    st.session_state.get("candidate_search_duration_minutes") or 120
                                ),
                                address=address_input,
                                current_user_name=st.session_state.get("current_user_name"),
                            )
                            st.session_state.pop("_candidate_search_masters", None)
                        except FirestoreSaveError as e:
                            st.error(f"案件の作成に失敗しました: {e}")
                            st.session_state.pop(processing_key, None)
                            st.session_state[result_key] = "failed"
                            return
                        except FirestoreConnectionError:
                            st.error(DB_UNAVAILABLE_MESSAGE)
                            st.session_state.pop(processing_key, None)
                            st.session_state[result_key] = "failed"
                            return
                    gcal_tok = st.session_state.get("google_calendar_tokens") or {}
                    vf_sess = (
                        gcal_tok.get("vehicle_fleet")
                        if isinstance(gcal_tok, dict)
                        else None
                    )
                    try:
                        settings_for_commit = get_settings()
                    except FirestoreConnectionError:
                        settings_for_commit = {}
                    try:
                        with visible_spinner("カレンダーへ登録中…"):
                            ok, msgs, save_project_schedule, new_event_refs = (
                                commit_candidate_to_calendars(
                                    project=project_for_commit,
                                    candidate=target,
                                    workers=workers,
                                    vehicles=vehicles,
                                    session_tokens=st.session_state.get("google_calendar_tokens"),
                                    settings=settings_for_commit,
                                    vehicle_fleet_session=vf_sess,
                                    event_title=custom_title or None,
                                )
                            )
                    except Exception as exc:
                        st.error("カレンダー登録中にエラーが発生しました。")
                        st.exception(exc)
                        st.session_state.pop(processing_key, None)
                        st.session_state[result_key] = "failed"
                        return
                    if not save_project_schedule:
                        for m in msgs:
                            st.warning(m)
                        st.session_state.pop(processing_key, None)
                        st.session_state[result_key] = "failed"
                        st.rerun()
                    if project_for_commit:
                        tz = ZoneInfo("Asia/Tokyo")
                        sa = start_at_d
                        ea = end_at_d
                        if sa.tzinfo is None:
                            sa = sa.replace(tzinfo=tz)
                        else:
                            sa = sa.astimezone(tz)
                        if ea.tzinfo is None:
                            ea = ea.replace(tzinfo=tz)
                        else:
                            ea = ea.astimezone(tz)
                        try:
                            patch_fields: Dict[str, Any] = {
                                "scheduled_start_at": sa.isoformat(),
                                "scheduled_end_at": ea.isoformat(),
                            }
                            # 全カレンダー登録成功時のみイベントIDを保存（部分成功で上書きすると不整合）
                            if ok:
                                patch_fields["google_calendar_event_refs"] = new_event_refs
                            patch_project_fields(
                                str(project_for_commit["project_id"]),
                                patch_fields,
                                current_user_name=st.session_state.get("current_user_name"),
                            )
                        except FirestoreSaveError as e:
                            st.error(f"案件の保存に失敗しました: {e}")
                            st.session_state.pop(processing_key, None)
                            st.session_state[result_key] = "failed"
                            return
                        except FirestoreConnectionError:
                            st.error(DB_UNAVAILABLE_MESSAGE)
                            st.session_state.pop(processing_key, None)
                            st.session_state[result_key] = "failed"
                            return
                    elif msgs:
                        for m in msgs:
                            if m.strip():
                                st.info(m)
                    st.session_state[result_key] = "success" if ok else "partial"
                    st.session_state.pop(processing_key, None)
                    st.rerun()

            _show_candidate_detail()

    if st.session_state.get("candidate_search_display_pending"):
        _finish_candidate_search_display_if_needed()
    elif (
        not st.session_state.get("candidate_search_job")
        and st.session_state.get("candidate_search_ui_busy")
    ):
        _clear_candidate_search_ui_busy()
        inject_clear_force_busy_overlay()

if __name__ == "__main__":
    render_page()


