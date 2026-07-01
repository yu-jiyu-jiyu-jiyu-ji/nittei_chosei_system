"""検索可能コンボボックス（text_input 絞り込み + selectbox 確定、1窓表示）."""
from __future__ import annotations

import html
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
    """案件名入力で絞り込み、下の一覧で確定（CSS のみで1窓表示）."""
    qkey = _combo_query_key(select_key, query_key)
    marker_id = f"combo_anchor_{''.join(ch if ch.isalnum() else '_' for ch in select_key)}"

    selected = str(st.session_state.get(select_key) or "").strip()
    if selected and selected not in options:
        st.session_state[select_key] = ""
        selected = ""

    if qkey not in st.session_state:
        st.session_state[qkey] = selected

    if help:
        st.caption(help)

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
        cur = str(st.session_state.get(select_key) or "").strip()
        if cur not in filtered:
            st.session_state[select_key] = ""

    def _on_select_change() -> None:
        _sync_query_from_selection(select_key, qkey)

    q = str(st.session_state.get(qkey, "") or "").strip()
    st.session_state[f"{select_key}_draft"] = q
    filtered = filter_options(options, q) if q else list(options)

    show_picker = False
    if q and not filtered:
        st.session_state[select_key] = ""
    elif filtered:
        show_picker = True
        picker_options = [""] + filtered
        cur = str(st.session_state.get(select_key) or "").strip()
        if cur and cur not in picker_options:
            st.session_state[select_key] = ""

    combo_mode = "dual" if show_picker else "single"
    st.markdown(
        f"""
<style>
  #{marker_id} {{
    display: none;
  }}
  #{marker_id} + [data-testid="stElementContainer"] {{
    margin-bottom: 0 !important;
    padding-bottom: 0 !important;
  }}
  #{marker_id} + [data-testid="stElementContainer"] [data-testid="stTextInput"] input {{
    font-size: 16px !important;
    background: #fff !important;
  }}
  #{marker_id} + [data-testid="stElementContainer"] [data-testid="InputInstructions"] {{
    display: none !important;
  }}
  #{marker_id}.combo-dual + [data-testid="stElementContainer"] [data-testid="stTextInput"] input {{
    border-bottom-left-radius: 0 !important;
    border-bottom-right-radius: 0 !important;
    border-bottom-color: rgba(49, 51, 63, 0.1) !important;
  }}
  #{marker_id}.combo-dual + [data-testid="stElementContainer"] + [data-testid="stElementContainer"] {{
    margin-top: -1.15rem !important;
    padding-top: 0 !important;
  }}
  #{marker_id}.combo-dual
    + [data-testid="stElementContainer"]
    + [data-testid="stElementContainer"] [data-testid="stSelectbox"] > div > div {{
    border-top-left-radius: 0 !important;
    border-top-right-radius: 0 !important;
    border-top: none !important;
    background: #fff !important;
  }}
  #{marker_id}.combo-dual
    + [data-testid="stElementContainer"]
    + [data-testid="stElementContainer"] [data-testid="stSelectbox"] label {{
    display: none !important;
  }}
  .candidate-combo-selected-badge {{
    display: inline-block;
    margin: 0.15rem 0 0.5rem;
    padding: 0.3rem 0.65rem;
    border-radius: 0.35rem;
    background: rgba(28, 131, 225, 0.12);
    color: rgb(29, 79, 126);
    font-size: 0.875rem;
    font-weight: 600;
  }}
</style>
<div id="{marker_id}" class="combo-{combo_mode}" aria-hidden="true"></div>
""",
        unsafe_allow_html=True,
    )

    st.text_input(
        label,
        key=qkey,
        placeholder=placeholder,
        label_visibility="visible",
        on_change=_on_query_change,
    )

    if q and not filtered:
        st.caption("該当する案件がありません。キーワードを変えてください。")
    elif show_picker:
        st.selectbox(
            "一覧から選択",
            options=picker_options,
            format_func=lambda v: empty_label if not v else v,
            key=select_key,
            label_visibility="collapsed",
            on_change=_on_select_change,
        )

    final = str(st.session_state.get(select_key) or "").strip()
    if final in options:
        st.markdown(
            f'<div class="candidate-combo-selected-badge">選択中: {html.escape(final)}</div>',
            unsafe_allow_html=True,
        )
        return final
    return ""
