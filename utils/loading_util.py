"""読み込み中UI。

visible_spinner: 処理ブロック用（経過時間・stretch 幅）。
全画面の読み込みレイヤーは layout_util の inject_wide_layout 内で注入される。
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import streamlit as st


@contextmanager
def visible_spinner(text: str) -> Iterator[None]:
    """st.spinner のラッパー。経過秒表示と stretch 幅で誤タップを抑える."""
    with st.spinner(text, show_time=True, width="stretch"):
        yield
