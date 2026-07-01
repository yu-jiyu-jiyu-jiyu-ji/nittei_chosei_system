"""検索可能コンボボックス（text_input + 絞り込み一覧。スマホでもキーボード表示）."""
from __future__ import annotations

from typing import List, Optional

import streamlit as st


def filter_options(options: List[str], query: str) -> List[str]:
    """部分一致で options を絞り込む."""
    q = query.strip().casefold()
    if not q:
        return list(options)
    return [o for o in options if q in o.casefold()]


def _combo_query_key(select_key: str, query_key: Optional[str]) -> str:
    return query_key or f"{select_key}_query"


def _combo_draft_keys(select_key: str, query_key: Optional[str]) -> List[str]:
    qkey = _combo_query_key(select_key, query_key)
    return [f"{select_key}_draft", qkey]


def resolve_combo_selection(
    select_key: str,
    options: List[str],
    *,
    query_key: Optional[str] = None,
) -> str:
    """確定値・入力文字から案件名を解決して session_state に反映する."""
    current = str(st.session_state.get(select_key) or "").strip()
    if current in options:
        return current

    draft = ""
    for key in _combo_draft_keys(select_key, query_key):
        draft = str(st.session_state.get(key) or "").strip()
        if draft:
            break
    if not draft:
        return ""

    for opt in options:
        if opt == draft:
            st.session_state[select_key] = opt
            return opt

    draft_cf = draft.casefold()
    exact_ci = [opt for opt in options if opt.casefold() == draft_cf]
    if len(exact_ci) == 1:
        st.session_state[select_key] = exact_ci[0]
        return exact_ci[0]

    partial = [opt for opt in options if draft_cf in opt.casefold()]
    if len(partial) == 1:
        st.session_state[select_key] = partial[0]
        return partial[0]

    return ""


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
    """案件検索窓: 入力で絞り込み、一覧から選択（従来の text_input + selectbox 相当）."""
    qkey = _combo_query_key(select_key, query_key)
    selected = str(st.session_state.get(select_key) or "").strip()
    if selected and selected not in options:
        st.session_state[select_key] = ""
        selected = ""

    if qkey not in st.session_state:
        st.session_state[qkey] = selected

    def _sync_query_to_selection() -> None:
        q = str(st.session_state.get(qkey, "") or "").strip()
        st.session_state[f"{select_key}_draft"] = q
        if not q:
            st.session_state[select_key] = ""
            return
        if q in options:
            st.session_state[select_key] = q
            return
        resolve_combo_selection(select_key, options, query_key=query_key)

    def _sync_selection_to_query() -> None:
        sel = str(st.session_state.get(select_key, "") or "").strip()
        if sel and sel in options:
            st.session_state[qkey] = sel
            st.session_state[f"{select_key}_draft"] = sel

    query = st.text_input(
        label,
        key=qkey,
        placeholder=placeholder,
        help=help,
        on_change=_sync_query_to_selection,
    )
    q = str(query or "").strip()
    st.session_state[f"{select_key}_draft"] = q

    if not q:
        st.session_state[select_key] = ""
    elif q in options:
        st.session_state[select_key] = q
    else:
        filtered = filter_options(options, q)
        if len(filtered) == 1:
            st.session_state[select_key] = filtered[0]
        else:
            resolved = resolve_combo_selection(select_key, options, query_key=query_key)
            if not resolved and selected not in filtered:
                st.session_state[select_key] = ""

    filtered = filter_options(options, q)
    show_picker = bool(q and len(filtered) > 1)
    if show_picker:
        picker_options = [""] + filtered
        cur = str(st.session_state.get(select_key) or "").strip()
        if cur not in picker_options:
            st.session_state[select_key] = ""
        st.selectbox(
            "一覧から選択",
            options=picker_options,
            format_func=lambda v: empty_label if not v else v,
            key=select_key,
            label_visibility="collapsed",
            on_change=_sync_selection_to_query,
        )
    elif q and not filtered:
        st.caption("該当する案件がありません。キーワードを変えてください。")
    elif q and len(filtered) == 1:
        st.session_state[select_key] = filtered[0]

    if show_picker:
        final = str(st.session_state.get(select_key) or "").strip()
        return final if final in options else ""

    resolved = resolve_combo_selection(select_key, options, query_key=query_key)
    if resolved:
        return resolved

    final = str(st.session_state.get(select_key) or "").strip()
    return final if final in options else ""
