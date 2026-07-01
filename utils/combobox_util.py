"""検索可能コンボボックス（text_input 絞り込み + selectbox 確定）."""
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


def resolve_combo_selection(
    select_key: str,
    options: List[str],
    *,
    query_key: Optional[str] = None,
) -> str:
    """確定済み・一意に解決できる入力から案件名を返す."""
    current = str(st.session_state.get(select_key) or "").strip()
    if current in options:
        return current

    qkey = _combo_query_key(select_key, query_key)
    draft = str(st.session_state.get(qkey) or "").strip()
    if not draft:
        draft = str(st.session_state.get(f"{select_key}_draft") or "").strip()
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


def _sync_query_from_selection(select_key: str, qkey: str) -> None:
    sel = str(st.session_state.get(select_key) or "").strip()
    if sel:
        st.session_state[qkey] = sel
        st.session_state[f"{select_key}_draft"] = sel


def render_searchable_selectbox(
    label: str,
    options: List[str],
    *,
    select_key: str,
    query_key: Optional[str] = None,
    empty_label: str = "（一覧から選択）",
    placeholder: str = "案件名を入力して絞り込み・選択…",
    help: Optional[str] = None,
) -> str:
    """案件名入力で絞り込み、下の一覧で確定（Streamlit 標準ウィジェットで Python 連携）."""
    qkey = _combo_query_key(select_key, query_key)

    selected = str(st.session_state.get(select_key) or "").strip()
    if selected and selected not in options:
        st.session_state[select_key] = ""
        selected = ""

    if qkey not in st.session_state:
        st.session_state[qkey] = selected

    st.markdown(
        """
<style>
  .candidate-combo-shell [data-testid="stTextInput"] input {
    font-size: 16px !important;
    border-bottom-left-radius: 0 !important;
    border-bottom-right-radius: 0 !important;
    border-bottom-color: rgba(49, 51, 63, 0.12) !important;
  }
  .candidate-combo-shell [data-testid="stSelectbox"] {
    margin-top: -1rem;
  }
  .candidate-combo-shell [data-testid="stSelectbox"] > div > div {
    border-top-left-radius: 0 !important;
    border-top-right-radius: 0 !important;
    border-top: none !important;
  }
  .candidate-combo-shell [data-testid="stSelectbox"] label {
    display: none !important;
  }
</style>
""",
        unsafe_allow_html=True,
    )
    st.markdown('<div class="candidate-combo-shell">', unsafe_allow_html=True)

    def _on_query_change() -> None:
        q = str(st.session_state.get(qkey, "") or "").strip()
        st.session_state[f"{select_key}_draft"] = q
        if not q:
            st.session_state[select_key] = ""
            return
        if q in options:
            st.session_state[select_key] = q
            return
        filtered = filter_options(options, q)
        if len(filtered) == 1:
            st.session_state[select_key] = filtered[0]
            return
        cur = str(st.session_state.get(select_key) or "").strip()
        if cur not in filtered:
            st.session_state[select_key] = ""

    def _on_select_change() -> None:
        _sync_query_from_selection(select_key, qkey)

    st.text_input(
        label,
        key=qkey,
        placeholder=placeholder,
        help=help,
        on_change=_on_query_change,
    )

    q = str(st.session_state.get(qkey, "") or "").strip()
    st.session_state[f"{select_key}_draft"] = q
    filtered = filter_options(options, q) if q else list(options)

    if q and len(filtered) == 1:
        st.session_state[select_key] = filtered[0]
    elif q and not filtered:
        st.caption("該当する案件がありません。キーワードを変えてください。")
        st.session_state[select_key] = ""
    elif filtered:
        picker_options = [""] + filtered
        cur = str(st.session_state.get(select_key) or "").strip()
        if cur and cur not in picker_options:
            st.session_state[select_key] = ""
        st.selectbox(
            "一覧から選択",
            options=picker_options,
            format_func=lambda v: empty_label if not v else v,
            key=select_key,
            label_visibility="collapsed",
            on_change=_on_select_change,
        )

    st.markdown("</div>", unsafe_allow_html=True)

    resolved = resolve_combo_selection(select_key, options, query_key=query_key)
    final = resolved or str(st.session_state.get(select_key) or "").strip()
    if final in options:
        st.info(f"選択中: {final}")
        return final
    return ""
