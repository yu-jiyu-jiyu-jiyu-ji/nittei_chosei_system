"""検索可能コンボボックス（入力で絞り込み + 一覧から選択）."""
from __future__ import annotations

from typing import List, Optional

import streamlit as st


def filter_options(options: List[str], query: str, *, pin: str = "") -> List[str]:
    """部分一致で options を絞り込む。pin は選択中の値を一覧に残すため."""
    q = query.strip().casefold()
    if q:
        filtered = [o for o in options if q in o.casefold()]
    else:
        filtered = list(options)
    if pin and pin in options and pin not in filtered:
        filtered = [pin, *filtered]
    return filtered


def render_searchable_selectbox(
    label: str,
    options: List[str],
    *,
    select_key: str,
    query_key: Optional[str] = None,
    empty_label: str = "（選択してください）",
    placeholder: str = "案件名の一部を入力して絞り込み…",
    help: Optional[str] = None,
) -> str:
    """text_input で絞り込み、selectbox で確定選択するコンボボックス."""
    qkey = query_key or f"{select_key}_query"
    if qkey not in st.session_state:
        st.session_state[qkey] = str(st.session_state.get(select_key, "") or "")

    def _on_query_change() -> None:
        q = str(st.session_state.get(qkey, "") or "").strip()
        if not q:
            st.session_state[select_key] = ""
            return
        if q in options:
            st.session_state[select_key] = q

    def _on_select_change() -> None:
        sel = str(st.session_state.get(select_key, "") or "")
        if sel:
            st.session_state[qkey] = sel
        elif not str(st.session_state.get(qkey, "") or "").strip():
            st.session_state[qkey] = ""

    query = st.text_input(
        label,
        key=qkey,
        placeholder=placeholder,
        help=help,
        on_change=_on_query_change,
    )
    q = str(query or "").strip()
    pinned = str(st.session_state.get(select_key, "") or "")
    filtered = filter_options(options, q)
    if pinned and pinned not in filtered:
        st.session_state[select_key] = ""
        pinned = ""

    if not filtered:
        st.caption("該当する案件がありません。キーワードを変えてください。")
        return ""

    st.selectbox(
        f"{label}（一覧）",
        options=[""] + filtered,
        format_func=lambda v: empty_label if not v else v,
        key=select_key,
        label_visibility="collapsed",
        on_change=_on_select_change,
    )
    if len(filtered) < len(options):
        st.caption(f"表示: {len(filtered)} / {len(options)} 件（入力で絞り込み中）")

    selected = str(st.session_state.get(select_key, "") or "")
    return selected if selected in options else ""
