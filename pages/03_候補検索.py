from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import plotly.graph_objects as go
import streamlit as st
import pandas as pd

from config.constants import APP_TITLE, DB_UNAVAILABLE_MESSAGE
from services.candidate_search_service import (
    apply_previous_location_overrides_to_calendars,
    collect_missing_previous_locations,
    collect_week_busy_events,
    fetch_week_calendar_events_bundle,
    format_week_events_jst_table_rows,
    search_candidates,
    sunday_week_containing,
    work_hours_display_hours,
)
from services.firestore_service import FirestoreConnectionError, FirestoreSaveError
from services.project_service import list_projects as list_projects_from_service, patch_project_fields
from services.schedule_commit_service import (
    commit_candidate_to_calendars,
    remove_project_schedule_from_google,
)
from services.setting_service import get_settings
from services.vehicle_service import list_vehicles
from services.worker_service import list_workers
from utils.layout_util import STREAMLIT_MENU_ITEMS, inject_sidebar_nav, inject_wide_layout
from utils.session_util import init_session_state


_YOUBI = ("月", "火", "水", "木", "金", "土", "日")


def _weekday_label_calendar_header(d: date) -> str:
    """週カレンダー列見出し（1行: 「3/22 日」形式。表形式カレンダーと同じ読み方）."""
    return f"{d.month}/{d.day} {_YOUBI[d.weekday()]}"


def _format_date_jp(d: date) -> str:
    """詳細ダイアログ用の日付（和文の読みやすい表記）."""
    return f"{d.year}年{d.month}月{d.day}日（{_YOUBI[d.weekday()]}）"


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


def _render_calendar_table_header_html(week_dates: List[date]) -> None:
    """Plotly 軸ラベルだけでは小さく見えるため、表形式の週見出し行をチャート直上に表示する."""
    parts: List[str] = []
    for i, d in enumerate(week_dates):
        label = _weekday_label_calendar_header(d)
        bg = _column_bg_color(d)
        border = "border-right:1px solid #d8d8d8;" if i < 6 else ""
        parts.append(
            f'<div style="text-align:center;padding:9px 4px;font-size:14px;color:#222;'
            f'font-weight:500;background:{bg};{border}">{label}</div>'
        )
    inner = "".join(parts)
    html = (
        '<div style="display:flex;width:100%;align-items:stretch;margin:0;padding:0;'
        'box-sizing:border-box;">'
        '<div style="width:56px;min-width:56px;flex-shrink:0;"></div>'
        '<div style="flex:1;display:grid;grid-template-columns:repeat(7,minmax(0,1fr));'
        "border:1px solid #d8d8d8;border-bottom:none;"
        'box-sizing:border-box;">'
        f"{inner}"
        "</div>"
        '<div style="width:16px;min-width:16px;flex-shrink:0;"></div>'
        "</div>"
    )
    st.markdown(html, unsafe_allow_html=True)


PLOTLY_CALENDAR_KEY = "candidate_week_plot"


def _apply_plotly_point_selection(plot_state: Any, ordered_ids: List[str]) -> None:
    """Plotly のクリック選択から候補IDを取り、ダイアログ用セッションに入れる."""
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
    if not cid and p0.get("point_index") is not None:
        idx = int(p0["point_index"])
        if 0 <= idx < len(ordered_ids):
            cid = ordered_ids[idx]
    if cid:
        st.session_state["candidate_dialog_id"] = cid


def _build_candidate_week_plotly_figure(
    *,
    candidates: List[Dict[str, Any]],
    week_start_date: date,
    slot_minutes: int,
    day_start_hour: int,
    day_end_hour: int,
    worker_id_to_name: Dict[str, str],
    vehicle_id_to_name: Dict[str, str],
    hide_xaxis_tick_labels: bool = False,
) -> tuple[go.Figure, List[str]]:
    """週間候補を Plotly で描画（セル＝マーカー。クリックで候補IDを取得可能）."""
    week_dates = [week_start_date + timedelta(days=i) for i in range(7)]
    valid_dates = {d.isoformat() for d in week_dates}

    start_minutes = day_start_hour * 60
    end_minutes = day_end_hour * 60
    total_minutes = max(1, end_minutes - start_minutes)

    xs: List[float] = []
    ys: List[float] = []
    texts: List[str] = []
    hover_texts: List[str] = []
    customdata: List[str] = []
    sizes: List[float] = []
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
        if day_idx < 0 or day_idx > 6:
            continue

        start_m = sa.hour * 60 + sa.minute
        end_m = ea.hour * 60 + ea.minute
        top_m = max(0, start_m - start_minutes)
        height_m = max(
            slot_minutes,
            min(end_minutes, end_m) - max(start_minutes, start_m),
        )
        if height_m <= 0:
            continue

        mid_m = top_m + height_m / 2.0
        dur_m = max(slot_minutes, end_m - start_m)

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

        xs.append(float(day_idx))
        ys.append(mid_m)
        texts.append(sa.strftime("%H:%M"))
        customdata.append(cid)
        ordered_ids.append(cid)
        # 枠が大きすぎると背後の Y 軸目盛りが透けて「縦に時刻が並んだ」ように見えるため上限を抑える
        sizes.append(float(max(14.0, min(40.0, (dur_m / 60.0) * 20.0))))

    tickvals: List[int] = []
    ticktext: List[str] = []
    slot_count = max(1, (total_minutes + slot_minutes - 1) // slot_minutes)
    for i in range(slot_count + 1):
        m_abs = start_minutes + i * slot_minutes
        off = i * slot_minutes
        if off > total_minutes:
            break
        if m_abs % 60 != 0:
            continue
        tickvals.append(off)
        hh = m_abs // 60
        mm = m_abs % 60
        ticktext.append(f"{hh}:{mm:02d}")

    # 列の中央ではなく「セル境界」の縦線に揃える（x=±0.5, 1.5, …）。背景は平日白・土水色・日祝赤。
    grid_line = "#d8d8d8"
    layout_shapes: List[Dict[str, Any]] = []
    for i in range(7):
        dcol = week_dates[i]
        layout_shapes.append(
            {
                "type": "rect",
                "xref": "x",
                "yref": "y",
                "x0": i - 0.5,
                "x1": i + 0.5,
                "y0": 0,
                "y1": total_minutes,
                "fillcolor": _column_bg_color(dcol),
                "line": {"width": 0},
                "layer": "below",
            }
        )
    for xv in (-0.5, 0.5, 1.5, 2.5, 3.5, 4.5, 5.5, 6.5):
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

    fig = go.Figure()
    if xs:
        fig.add_trace(
            go.Scatter(
                x=xs,
                y=ys,
                mode="markers+text",
                marker=dict(
                    size=sizes,
                    color="#15d6d6",
                    symbol="square",
                    opacity=1.0,
                    line=dict(width=1, color="rgba(0,0,0,0.12)"),
                ),
                text=texts,
                textposition="middle center",
                textfont=dict(size=10, color="#022"),
                customdata=customdata,
                hovertext=hover_texts,
                hoverinfo="text",
                name="候補",
            )
        )

    fig.update_layout(
        height=520,
        shapes=layout_shapes,
        # 週見出しは HTML 行で表示する場合は Plotly の上ラベルを隠し、余白を詰める
        margin=dict(
            l=56,
            r=16,
            t=10 if hide_xaxis_tick_labels else 52,
            b=24,
        ),
        paper_bgcolor="#fff",
        plot_bgcolor="#ffffff",
        showlegend=False,
        dragmode=False,
        xaxis=dict(
            side="top",
            tickmode="array",
            tickvals=list(range(7)),
            ticktext=(
                [""] * 7
                if hide_xaxis_tick_labels
                else [_weekday_label_calendar_header(d) for d in week_dates]
            ),
            showticklabels=not hide_xaxis_tick_labels,
            range=[-0.5, 6.5],
            # 既定の縦グリッドは tick（列の中央）に引かれるためオフ。縦線は shapes でセル境界に描く。
            showgrid=False,
            zeroline=False,
            fixedrange=True,
            automargin=True,
            tickfont=dict(size=12, color="#111"),
            showline=True,
            linecolor=grid_line,
        ),
        yaxis=dict(
            title="",
            range=[0, total_minutes],
            autorange="reversed",
            tickmode="array",
            tickvals=tickvals,
            ticktext=ticktext,
            showgrid=True,
            gridcolor="#c8c8c8",
            dtick=None,
            zeroline=False,
            fixedrange=True,
        ),
    )
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
    """週間候補カレンダー（Plotly）。セルをクリックすると候補詳細ダイアログが開く（ページ遷移なし）。"""
    wd: date = week_start_date
    if isinstance(wd, datetime):
        wd = wd.date()
    week_dates = [wd + timedelta(days=i) for i in range(7)]
    _render_calendar_table_header_html(week_dates)
    fig, ordered_ids = _build_candidate_week_plotly_figure(
        candidates=candidates,
        week_start_date=wd,
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
    )
    _apply_plotly_point_selection(plot_state, ordered_ids)
    note = footer_note or (
        "※ 色ブロックは「空きとして採用した候補」の開始〜終了です。"
        "（上の「この週のカレンダー予定」で参照IDを確認できます）"
    )
    st.caption(note)


def render_page() -> None:
    """候補検索画面."""
    st.set_page_config(
        page_title=f"{APP_TITLE} - 候補検索",
        layout="wide",
        menu_items=STREAMLIT_MENU_ITEMS,
    )
    init_session_state()
    # 候補検索ページへ再入場したときは、前回候補を残さず毎回リフレッシュする。
    if st.session_state.get("_active_page_id") != "candidate_search":
        st.session_state.pop("candidate_results", None)
        st.session_state.pop("candidate_search_job", None)
        st.session_state.pop("candidate_dialog_id", None)
        st.session_state.pop("week_nav_trigger_search", None)
    st.session_state["_active_page_id"] = "candidate_search"
    inject_wide_layout()
    inject_sidebar_nav()

    st.title("候補検索")
    st.caption("案件条件をもとに、予定を入れても問題ない候補日時を検索します。")

    notice = st.session_state.pop("schedule_commit_notice", None)
    if notice:
        st.success(notice)
    for _w in st.session_state.pop("candidate_search_warnings_flash", []) or []:
        st.warning(_w)
    # 旧実装の ?candidate_id= リンクは multipage で白画面になることがあるため廃止。残っていればクエリだけ除去して案内する。
    if "candidate_id" in st.query_params:
        try:
            del st.query_params["candidate_id"]
        except Exception:
            pass
        st.info("候補はカレンダー上の色ブロックをクリックして開きます。古いブックマークのクエリは無視しました。")

    week_nav_trigger = st.session_state.pop("week_nav_trigger_search", False)
    if "candidate_calendar_week_start" not in st.session_state:
        st.session_state["candidate_calendar_week_start"] = sunday_week_containing(date.today())

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

    # 案件・職人・車両は Firestore（またはダミーフォールバック）から取得
    with st.spinner("データを読み込み中…"):
        try:
            _all_projects = list_projects_from_service({})
            # 対応済み（リフォーム完了）は日程候補の対象外
            projects = [p for p in _all_projects if str(p.get("status") or "") != "completed"]
        except FirestoreConnectionError:
            st.error(DB_UNAVAILABLE_MESSAGE)
            return
        except Exception as exc:
            st.error("案件一覧の取得中に想定外エラーが発生しました。")
            st.exception(exc)
            return

        try:
            workers = list_workers()
        except FirestoreConnectionError:
            st.error(DB_UNAVAILABLE_MESSAGE)
            return
        except Exception as exc:
            st.error("職人一覧の取得中に想定外エラーが発生しました。")
            st.exception(exc)
            return

        try:
            vehicles = list_vehicles()
        except FirestoreConnectionError:
            st.error(DB_UNAVAILABLE_MESSAGE)
            return
        except Exception as exc:
            st.error("車両一覧の取得中に想定外エラーが発生しました。")
            st.exception(exc)
            return

    # ----------------------------
    # 上部：検索条件（画像UIの再現）
    # ----------------------------
    st.subheader("条件")
    st.caption(
        "ステータスが「対応済み（リフォーム完了）」の案件は、日程候補の対象外のためここには表示されません。"
    )

    project_options = {p["project_name"]: p for p in projects}
    project_name_list = list(project_options.keys())

    # 案件（横幅いっぱい）
    selected_project_name = st.selectbox(
        "案件",
        options=[""] + project_name_list,
        format_func=lambda v: v if v else "（選択してください）",
        key="candidate_search_project_select",
    )
    selected_project = project_options.get(selected_project_name)

    # 人数（- / 入力 / +）と 職人（選択 + 含む/含まない）と ボタン（右寄せ）
    if "candidate_search_capacity" not in st.session_state:
        st.session_state["candidate_search_capacity"] = 0

    # 案件を変えたときは、人数を案件の「必要人数」で揃える（0 のまま検索できないのを防ぐ）
    _prev_proj_key = st.session_state.get("_candidate_sync_project_key")
    _cur_proj_key = selected_project_name or ""
    if _cur_proj_key != _prev_proj_key:
        st.session_state["_candidate_sync_project_key"] = _cur_proj_key
        if selected_project:
            try:
                rw = int(selected_project.get("required_workers") or 0)
                st.session_state["candidate_search_capacity"] = max(0, rw)
            except (TypeError, ValueError):
                pass
        else:
            st.session_state["candidate_search_capacity"] = 0

    worker_label_to_id = {w["name"]: w["worker_id"] for w in workers}
    worker_names = list(worker_label_to_id.keys())

    # 画像では「職人  Aさん  を含む」のイメージなので、職人は単一選択（未選択可）＋含む/含まない
    # 条件行をラップして横並び指定用のクラスを付与
    st.markdown('<div class="nowrap-row">', unsafe_allow_html=True)
    col_cap, col_worker, col_buttons = st.columns([2.2, 4.8, 2.0])
    with col_cap:
        st.write("人数")
        # text_input と別キーで上書きされていたため ± が効かなかった。number_input で同一キーに統一する。
        st.number_input(
            "人数の値",
            min_value=0,
            step=1,
            key="candidate_search_capacity",
            label_visibility="collapsed",
        )

    with col_worker:
        st.write("職人")
        w1, w2 = st.columns([3.0, 2.0])
        with w1:
            selected_worker_name = st.selectbox(
                " ",
                options=[""] + worker_names,
                format_func=lambda v: v if v else "（指定なし）",
                label_visibility="collapsed",
                key="worker_single_select",
            )
        with w2:
            include_mode = st.selectbox(
                " ",
                options=["含む", "含まない"],
                label_visibility="collapsed",
                key="worker_include_mode",
            )

        selected_worker_ids: List[str] = (
            [worker_label_to_id[selected_worker_name]] if selected_worker_name else []
        )

    with col_buttons:
        st.write("")
        st.write("")
        # PCでも改行しにくいよう、列幅を少し広めに
        b1, b2 = st.columns([1.2, 1.2])
        with b1:
            clear_clicked = st.button("クリア", use_container_width=True)
        with b2:
            search_clicked = st.button("検索", type="primary", use_container_width=True)

    # nowrap-row の閉じタグ
    st.markdown("</div>", unsafe_allow_html=True)

    required_capacity = int(st.session_state.get("candidate_search_capacity", 0))
    loc_ov: Dict[str, str] = st.session_state.setdefault("candidate_location_overrides", {})

    # 分割検索: ①カレンダーAPIは週1回 ②以降は同一データで1日ずつ計算（タイムアウトしにくく、以前の7倍取得もしない）
    cjob = st.session_state.get("candidate_search_job")
    if cjob is not None:
        step = int(cjob.get("step", -99))
        ws_job = cjob["week_start"]
        excl_job = {str(x) for x in cjob.get("excluded", [])}
        must_inc_job = [str(x) for x in cjob.get("must_include", [])]
        pj_n = (cjob.get("project_name") or "").strip()
        proj_job = project_options.get(pj_n) if pj_n else None
        cap_job = int(cjob.get("required_capacity", 0))
        try:
            settings_job = get_settings()
        except FirestoreConnectionError:
            settings_job = {}
        gcal_tok = st.session_state.get("google_calendar_tokens") or {}
        vf_sess = gcal_tok.get("vehicle_fleet") if isinstance(gcal_tok, dict) else None

        if step == -1:
            with st.spinner("カレンダー取得中…"):
                bundle, wpre = fetch_week_calendar_events_bundle(
                    project=proj_job,
                    workers=workers,
                    vehicles=vehicles,
                    settings=settings_job,
                    ui_capacity=cap_job,
                    session_tokens=st.session_state.get("google_calendar_tokens"),
                    vehicle_fleet_session=vf_sess,
                    excluded_worker_ids=excl_job,
                    search_week_start=ws_job,
                )
            if wpre:
                cjob["warnings_acc"].extend(wpre)
            if bundle is None:
                st.session_state["candidate_search_warnings_flash"] = list(
                    dict.fromkeys(cjob.get("warnings_acc") or [])
                )
                st.session_state.pop("candidate_search_job", None)
                st.rerun()
            cjob["bundle"] = bundle
            cjob["step"] = 0
            st.rerun()
        elif step < 7:
            d = ws_job + timedelta(days=step)
            with st.spinner(f"検索中…（{step + 1}/7日）"):
                part, warns = search_candidates(
                    project=proj_job,
                    workers=workers,
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
            st.session_state.pop("candidate_search_job", None)
            st.session_state.pop("_week_nav_undo", None)
            for _k in list(st.session_state.keys()):
                if isinstance(_k, str) and _k.startswith("calendar_week_events_"):
                    del st.session_state[_k]
            st.rerun()

    if selected_project:
        missing_prev = collect_missing_previous_locations(
            project=selected_project,
            workers=workers,
            ui_capacity=required_capacity,
            session_tokens=st.session_state.get("google_calendar_tokens"),
            location_overrides=loc_ov,
            search_date=date.today(),
        )
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
            "worker_single_select",
            "worker_include_mode",
            "candidate_search_capacity",
            "candidate_location_overrides",
        ):
            if k in st.session_state:
                del st.session_state[k]
        if "candidate_results" in st.session_state:
            del st.session_state["candidate_results"]
        st.session_state.pop("candidate_search_job", None)
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

    # 週移動での再実行時にもカレンダーを維持
    if (
        not search_clicked
        and not week_nav_trigger
        and "candidate_results" not in st.session_state
        and not st.session_state.get("candidate_search_job")
    ):
        # 検索結果がないのに候補だけ開こうとした（URL直打ち等）
        if st.session_state.get("candidate_dialog_id"):
            st.warning("候補を表示するには、先に検索を実行してください。")
            st.session_state.pop("candidate_dialog_id", None)
        # 検索実行前はカレンダー枠だけ表示しない（画像に近い挙動）
        return

    # 案件未選択でも「人数が選択されている」場合は候補表示する（要望⑧）
    if not selected_project and required_capacity <= 0 and search_clicked:
        st.error("案件が選択されていません。検索を行う前に案件を選択するか、人数を指定してください。")
        return

    if week_nav_trigger and not selected_project and required_capacity <= 0:
        prev_ws = st.session_state.pop("_week_nav_undo", None)
        if prev_ws is not None:
            st.session_state["candidate_calendar_week_start"] = prev_ws
        st.error("週を移動して再検索するには、案件を選択するか人数を指定してください。")
        return

    try:
        # 検索ボタン／週ナビ → カレンダー1回取得＋7日分割計算（candidate_search_job ブロック）
        run_search = search_clicked or week_nav_trigger
        if run_search:
            excluded_for_real: set = set()
            if include_mode == "含まない" and selected_worker_ids:
                excluded_for_real = {str(x) for x in selected_worker_ids}
                must_include_worker_ids: List[str] = []
            else:
                must_include_worker_ids = selected_worker_ids

            st.session_state["candidate_search_job"] = {
                "step": -1,
                "accum": [],
                "warnings_acc": [],
                "week_start": st.session_state["candidate_calendar_week_start"],
                "project_name": selected_project_name or "",
                "required_capacity": required_capacity,
                "excluded": list(excluded_for_real),
                "must_include": list(must_include_worker_ids),
            }
            st.rerun()
        else:
            filtered = list(st.session_state.get("candidate_results") or [])
    except Exception as exc:
        # 想定外エラー
        st.error("候補検索中に想定外エラーが発生しました。")
        st.exception(exc)
        return

    st.subheader("候補")
    if not filtered:
        st.info(
            "候補が見つかりませんでした。案件・職人・車両の連携、人数、就業時間、またはカレンダー上の空き状況を確認してください。"
            "（特定の日だけ午前で途切れる場合は、その日の Google カレンダーに午後の予定が入っている可能性があります。）"
        )

    worker_id_to_name = {w["worker_id"]: w["name"] for w in workers}
    vehicle_id_to_name = {v["vehicle_id"]: v["name"] for v in vehicles}

    # ----------------------------
    # 下部：週カレンダー形式の候補表示（色ブロック）
    # ----------------------------
    # 週移動（前週/次週）— 押下時は表示週の7日分を再検索
    ws = st.session_state["candidate_calendar_week_start"]

    # 週ナビゲーション（＜ 3月 ＞）: ボタンで同一セッション内の rerun（タブ遷移しない）
    st.markdown('<div class="week-nav-wrap">', unsafe_allow_html=True)
    col_prev, col_month, col_next = st.columns([1.0, 2.0, 1.0])
    with col_prev:
        if st.button("＜", key="week_prev_btn"):
            st.session_state["_week_nav_undo"] = ws
            st.session_state["candidate_calendar_week_start"] = ws - timedelta(days=7)
            st.session_state["week_nav_trigger_search"] = True
            st.rerun()
    with col_month:
        st.markdown(
            f"<div style='text-align:left;font-weight:700;'>{ws.month}月</div>",
            unsafe_allow_html=True,
        )
    with col_next:
        if st.button("＞", key="week_next_btn"):
            st.session_state["_week_nav_undo"] = ws
            st.session_state["candidate_calendar_week_start"] = ws + timedelta(days=7)
            st.session_state["week_nav_trigger_search"] = True
            st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

    cache_key = f"calendar_week_events_{ws.isoformat()}"
    if cache_key not in st.session_state:
        with st.spinner("カレンダー予定を取得中…"):
            try:
                try:
                    settings_for_cal = get_settings()
                except FirestoreConnectionError:
                    settings_for_cal = {}
                gcal_tok2 = st.session_state.get("google_calendar_tokens") or {}
                vf_sess2 = (
                    gcal_tok2.get("vehicle_fleet") if isinstance(gcal_tok2, dict) else None
                )
                week_events, week_warns = collect_week_busy_events(
                    week_start=ws,
                    workers=workers,
                    vehicles=vehicles,
                    session_tokens=st.session_state.get("google_calendar_tokens"),
                    settings=settings_for_cal,
                    vehicle_fleet_session=vf_sess2,
                )
                st.session_state[cache_key] = week_events
                if week_warns:
                    st.session_state["candidate_search_warnings_flash"] = list(
                        dict.fromkeys(
                            (st.session_state.get("candidate_search_warnings_flash") or [])
                            + week_warns
                        )
                    )
            except Exception:
                st.session_state[cache_key] = []

    week_ev = st.session_state.get(cache_key) or []
    with st.expander(
        "この週のカレンダー予定（職人・車両マスタの参照カレンダーIDで取得）",
        expanded=False,
    ):
        st.caption(
            "ブラウザで開いている Google アカウントと、マスタのカレンダーID／OAuth が一致しないと、"
            "ここに表示される予定とブラウザの週表示が食い違うことがあります。"
        )
        if not week_ev:
            st.info(
                "この週で取得できた予定がありません。"
                "未連携・権限不足・参照カレンダーIDの誤りの可能性があります。"
            )
        else:
            st.dataframe(
                pd.DataFrame(format_week_events_jst_table_rows(week_ev)),
                hide_index=True,
                use_container_width=True,
            )

    footer_note = (
        "※ 青い枠は「カレンダー上の空きとして採用した候補」です（同一時刻に複数の職人の組み合わせがある場合は別枠として表示されます）。"
        "上の展開で参照IDに紐づく予定を確認できます。"
    )

    try:
        cal_settings = get_settings()
    except FirestoreConnectionError:
        cal_settings = {}
    dsh, deh = work_hours_display_hours(cal_settings)
    try:
        slot_gran = int(cal_settings.get("time_slot_minutes") or 30)
    except (TypeError, ValueError):
        slot_gran = 30

    _render_week_calendar(
        candidates=filtered or [],
        week_start_date=st.session_state["candidate_calendar_week_start"],
        slot_minutes=slot_gran,
        day_start_hour=dsh,
        day_end_hour=deh,
        worker_id_to_name=worker_id_to_name,
        vehicle_id_to_name=vehicle_id_to_name,
        footer_note=footer_note,
    )

    if filtered:
        st.caption("候補の色ブロックをクリックすると、詳細のポップアップが開きます。")

    dcid = st.session_state.get("candidate_dialog_id")
    if dcid and filtered:
        target = next((c for c in filtered if c.get("candidate_id") == dcid), None)
        if target is None:
            st.session_state.pop("candidate_dialog_id", None)
        else:
            start_at_d: datetime = target["start_at"]
            end_at_d: datetime = target.get("end_at") or start_at_d
            workers_text_d = "、".join(
                worker_id_to_name.get(wid, wid) for wid in target.get("worker_ids", [])
            )
            vehicles_text_d = "、".join(
                vehicle_id_to_name.get(vid, vid) for vid in target.get("vehicle_ids", [])
            )

            @st.dialog("候補の詳細")
            def _show_candidate_detail() -> None:
                st.write(f"**候補ID**: {target.get('candidate_id')}")
                st.write(f"**日付**: {_format_date_jp(start_at_d.date())}")
                st.write(
                    f"**時間帯**: {start_at_d.strftime('%H:%M')} 〜 {end_at_d.strftime('%H:%M')}"
                )
                st.write(f"**対応可能人数**: {target.get('capacity')} 人")
                st.write(f"**職人**: {workers_text_d or '-'}")
                st.write(f"**車両**: {vehicles_text_d or '-'}")
                tw_d = target.get("travel_to_site_minutes_by_worker") or {}
                if isinstance(tw_d, dict) and tw_d:
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
                processing_key = f"dialog_decide_processing_{dcid}"
                processing = bool(st.session_state.get(processing_key, False))
                if processing:
                    st.info("処理中です。しばらくお待ちください…")
                col_close, col_decide = st.columns(2)
                with col_close:
                    if st.button("閉じる", key=f"dialog_close_{dcid}", disabled=processing):
                        st.session_state.pop("candidate_dialog_id", None)
                        st.session_state.pop(PLOTLY_CALENDAR_KEY, None)
                        st.session_state.pop(processing_key, None)
                        st.rerun()
                with col_decide:
                    if st.button(
                        "決定",
                        type="primary",
                        key=f"dialog_decide_{dcid}",
                        disabled=processing,
                    ):
                        st.session_state[processing_key] = True
                        st.rerun()

                if processing:
                    if not selected_project:
                        st.error(
                            "案件が選択されていません。上部で案件を選んでから確定してください。"
                        )
                        st.session_state.pop(processing_key, None)
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
                        with st.spinner("カレンダーへ登録中…"):
                            ok, msgs, save_project_schedule, new_event_refs = (
                                commit_candidate_to_calendars(
                                    project=selected_project,
                                    candidate=target,
                                    workers=workers,
                                    vehicles=vehicles,
                                    session_tokens=st.session_state.get("google_calendar_tokens"),
                                    settings=settings_for_commit,
                                    vehicle_fleet_session=vf_sess,
                                )
                            )
                    except Exception as exc:
                        st.error("カレンダー登録中にエラーが発生しました。")
                        st.exception(exc)
                        st.session_state.pop(processing_key, None)
                        return
                    if msgs:
                        st.session_state["candidate_search_warnings_flash"] = list(
                            dict.fromkeys(
                                (st.session_state.get("candidate_search_warnings_flash") or [])
                                + msgs
                            )
                        )
                    if not save_project_schedule:
                        st.session_state.pop(processing_key, None)
                        st.session_state.pop("candidate_dialog_id", None)
                        st.session_state.pop(PLOTLY_CALENDAR_KEY, None)
                        st.rerun()
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
                            str(selected_project["project_id"]),
                            patch_fields,
                            current_user_name=st.session_state.get("current_user_name"),
                        )
                    except FirestoreSaveError as e:
                        st.error(f"案件の保存に失敗しました: {e}")
                        st.session_state.pop(processing_key, None)
                        return
                    except FirestoreConnectionError:
                        st.error(DB_UNAVAILABLE_MESSAGE)
                        st.session_state.pop(processing_key, None)
                        return
                    st.session_state["schedule_commit_notice"] = (
                        "カレンダーに予定を登録し、案件に予定日時を保存しました。"
                        if ok
                        else "一部のカレンダー登録に失敗しました。成功した内容を反映し、案件に予定日時を保存しました。"
                    )
                    st.session_state.pop(processing_key, None)
                    st.session_state.pop("candidate_dialog_id", None)
                    st.session_state.pop(PLOTLY_CALENDAR_KEY, None)
                    st.rerun()

            _show_candidate_detail()

if __name__ == "__main__":
    render_page()

