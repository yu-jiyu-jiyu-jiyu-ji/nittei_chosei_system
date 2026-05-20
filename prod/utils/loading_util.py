"""読み込み中UI。

visible_spinner: 処理ブロック用（経過時間・stretch 幅）。
全画面の読み込みレイヤーは layout_util の inject_wide_layout 内で注入される。
"""

from __future__ import annotations

import html
from contextlib import contextmanager
from typing import Iterator

import streamlit as st


@contextmanager
def visible_spinner(text: str) -> Iterator[None]:
    """st.spinner のラッパー。経過秒表示と stretch 幅で誤タップを抑える."""
    with st.spinner(text, show_time=True, width="stretch"):
        yield


def inject_force_busy_marker(title: str = "検索・カレンダー表示中…") -> None:
    """layout_util の全画面読み込みレイヤーを、再実行の合間も維持する."""
    safe_title = html.escape(str(title or "検索・カレンダー表示中…"), quote=True)
    st.markdown(
        f'<span id="_st_force_busy_marker" data-busy-title="{safe_title}" '
        'aria-hidden="true" style="display:none"></span>',
        unsafe_allow_html=True,
    )


def candidate_search_busy_active() -> bool:
    """候補検索フロー（分割検索〜カレンダー描画）が進行中か."""
    return bool(
        st.session_state.get("candidate_search_ui_busy")
        or st.session_state.get("candidate_search_job")
        or st.session_state.get("candidate_search_calendar_pending")
    )
