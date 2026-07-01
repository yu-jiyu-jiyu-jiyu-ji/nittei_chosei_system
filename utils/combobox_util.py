"""検索可能コンボボックス（Streamlit 標準 selectbox + filter_mode）."""
from __future__ import annotations

from typing import List, Optional

import streamlit as st


def filter_options(options: List[str], query: str) -> List[str]:
    """部分一致で options を絞り込む（テスト・補助用）."""
    q = query.strip().casefold()
    if not q:
        return list(options)
    return [o for o in options if q in o.casefold()]


def render_searchable_selectbox(
    label: str,
    options: List[str],
    *,
    select_key: str,
    query_key: Optional[str] = None,
    empty_label: str = "（選択してください）",
    placeholder: str = "案件名を入力して絞り込み・選択…",
    help: Optional[str] = None,
) -> str:
    """1つの selectbox 内で入力検索できるコンボボックス."""
    del query_key  # 旧実装互換

    if select_key in st.session_state and st.session_state.get(select_key) == "":
        st.session_state[select_key] = None
    st.session_state.pop(f"{select_key}_query", None)

    current = st.session_state.get(select_key)
    if current and current not in options:
        st.session_state[select_key] = None

    select_kwargs = {
        "placeholder": placeholder or empty_label,
        "filter_mode": "contains",
        "key": select_key,
        "help": help,
    }
    if select_key not in st.session_state:
        select_kwargs["index"] = None
    st.selectbox(label, options, **select_kwargs)
    selected = st.session_state.get(select_key)
    return str(selected) if selected in options else ""
