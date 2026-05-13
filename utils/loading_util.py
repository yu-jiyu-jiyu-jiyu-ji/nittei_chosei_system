"""読み込み中UI。経過時間と横幅で処理中であることを明確にする."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import streamlit as st


@contextmanager
def visible_spinner(text: str) -> Iterator[None]:
    """st.spinner のラッパー。経過秒表示と stretch 幅で誤タップを抑える."""
    with st.spinner(text, show_time=True, width="stretch"):
        yield
