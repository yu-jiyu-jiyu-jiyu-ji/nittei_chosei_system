"""読み込み中UI。

visible_spinner: 処理ブロック用（経過時間・stretch 幅）。
全画面の読み込みレイヤーは layout_util の inject_wide_layout 内で注入される。
"""

from __future__ import annotations

import html
import json
from contextlib import contextmanager
from typing import Iterator

import streamlit as st
import streamlit.components.v1 as components


@contextmanager
def visible_spinner(text: str) -> Iterator[None]:
    """st.spinner のラッパー。経過秒表示と stretch 幅で誤タップを抑える."""
    with st.spinner(text, show_time=True, width="stretch"):
        yield


def inject_force_busy_marker(title: str = "検索・カレンダー表示中…") -> None:
    """layout_util の全画面読み込みレイヤーを、再実行の合間も維持する（現ページのみ有効）."""
    safe_title = html.escape(str(title or "検索・カレンダー表示中…"), quote=True)
    page_id = html.escape(str(st.session_state.get("_active_page_id") or ""), quote=True)
    st.markdown(
        f'<span class="_st_force_busy_marker" data-st-page="{page_id}" '
        f'data-busy-title="{safe_title}" aria-hidden="true" style="display:none"></span>',
        unsafe_allow_html=True,
    )


def inject_clear_force_busy_overlay() -> None:
    """強制 busy マーカーと全画面オーバーレイを即時解除（検索完了・フラグ整理用）."""
    page_id = json.dumps(str(st.session_state.get("_active_page_id") or "app"))
    components.html(
        f"<!-- st-clear-force-busy:{page_id} -->\n"
        f"""
        <script>
        (function() {{
            var doc = window.parent.document;
            doc.querySelectorAll("#_st_force_busy_marker, ._st_force_busy_marker").forEach(function(el) {{
                el.remove();
            }});
            var S = doc._stGlobalBusyOverlay;
            if (S) {{
                if (S.hideSpinTimer) {{ clearTimeout(S.hideSpinTimer); S.hideSpinTimer = null; }}
                if (S.pendingTimer) {{ clearTimeout(S.pendingTimer); S.pendingTimer = null; }}
                S.state = "idle";
            }}
            var L = doc.getElementById("_st_global_busy_layer");
            if (L) L.classList.remove("_st_busy_on", "_st_busy_pending");
        }})();
        </script>
        """,
        height=0,
    )


def format_search_progress_pct(step_index: int, total_days: int) -> str:
    """分割検索の進捗を百分率文字列にする（例: 43%）."""
    if total_days <= 0:
        return "0%"
    pct = int(round(((int(step_index) + 1) / float(total_days)) * 100))
    pct = min(100, max(0, pct))
    return f"{pct}%"


def candidate_search_busy_active() -> bool:
    """候補検索フロー（分割検索〜カレンダー描画）が進行中か."""
    return bool(
        st.session_state.get("candidate_search_ui_busy")
        or st.session_state.get("candidate_search_job")
        or st.session_state.get("candidate_search_calendar_pending")
        or st.session_state.get("candidate_search_display_pending")
    )
