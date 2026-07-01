"""検索可能コンボボックス（text_input 絞り込み + selectbox 確定、1窓表示）."""
from __future__ import annotations

import json
from typing import List, Optional

import streamlit as st
import streamlit.components.v1 as components


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
    """案件名入力で絞り込み、下の一覧で確定（1窓に見せる CSS + JS）."""
    qkey = _combo_query_key(select_key, query_key)
    widget_id = "".join(ch if ch.isalnum() else "_" for ch in select_key)
    marker_id = f"combo_unify_{widget_id}"

    selected = str(st.session_state.get(select_key) or "").strip()
    if selected and selected not in options:
        st.session_state[select_key] = ""
        selected = ""

    if qkey not in st.session_state:
        st.session_state[qkey] = selected

    if help:
        st.caption(help)

    st.markdown(
        f'<div id="{marker_id}" class="candidate-combo-start" style="display:none;"></div>',
        unsafe_allow_html=True,
    )

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
        label_visibility="visible",
        on_change=_on_query_change,
    )

    q = str(st.session_state.get(qkey, "") or "").strip()
    st.session_state[f"{select_key}_draft"] = q
    filtered = filter_options(options, q) if q else list(options)

    show_picker = False
    if q and len(filtered) == 1:
        st.session_state[select_key] = filtered[0]
    elif q and not filtered:
        st.caption("該当する案件がありません。キーワードを変えてください。")
        st.session_state[select_key] = ""
    elif filtered:
        show_picker = True
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

    marker_id_js = json.dumps(marker_id)
    show_picker_js = "true" if show_picker else "false"
    components.html(
        f"""
<script>
(function() {{
  const MARKER_ID = {marker_id_js};
  const EXPECT_PICKER = {show_picker_js};
  const doc = window.parent && window.parent.document ? window.parent.document : document;

  function ensureStyles() {{
    if (doc.getElementById("candidate-combo-unified-styles")) return;
    const style = doc.createElement("style");
    style.id = "candidate-combo-unified-styles";
    style.textContent = `
      .candidate-combo-unified {{
        border: 1px solid rgba(49, 51, 63, 0.2);
        border-radius: 0.5rem;
        overflow: hidden;
        background: #fff;
        margin-bottom: 0.35rem;
      }}
      .candidate-combo-unified [data-testid="stElementContainer"] {{
        margin: 0 !important;
        padding-top: 0 !important;
        padding-bottom: 0 !important;
      }}
      .candidate-combo-unified [data-testid="stVerticalBlock"] {{
        gap: 0 !important;
      }}
      .candidate-combo-unified [data-testid="stWidgetLabel"] {{
        padding: 0.5rem 0.75rem 0.25rem !important;
        margin: 0 !important;
      }}
      .candidate-combo-unified [data-testid="stWidgetLabel"] p {{
        font-size: 0.875rem !important;
        font-weight: 600 !important;
        color: rgb(49, 51, 63) !important;
        margin: 0 !important;
      }}
      .candidate-combo-unified [data-testid="stTextInput"] {{
        margin: 0 !important;
        padding: 0 !important;
      }}
      .candidate-combo-unified [data-testid="stTextInput"] > div {{
        padding: 0 0.75rem 0.5rem !important;
      }}
      .candidate-combo-unified [data-testid="stTextInput"] input {{
        border: none !important;
        border-radius: 0 !important;
        box-shadow: none !important;
        font-size: 16px !important;
        padding: 0.35rem 0 !important;
        height: 2rem !important;
        min-height: 2rem !important;
        background: transparent !important;
      }}
      .candidate-combo-unified [data-testid="stTextInput"] input:focus {{
        outline: none !important;
        box-shadow: none !important;
      }}
      .candidate-combo-unified:not(.candidate-combo-unified--input-only)
        [data-testid="stTextInput"] > div {{
        border-bottom: 1px solid rgba(49, 51, 63, 0.1) !important;
      }}
      .candidate-combo-unified [data-testid="InputInstructions"] {{
        display: none !important;
      }}
      .candidate-combo-unified [data-testid="stSelectbox"] {{
        margin: 0 !important;
        padding: 0 !important;
      }}
      .candidate-combo-unified [data-testid="stSelectbox"] > div {{
        padding: 0 0.75rem 0.5rem !important;
      }}
      .candidate-combo-unified [data-testid="stSelectbox"] > div > div {{
        border: none !important;
        border-radius: 0 !important;
        box-shadow: none !important;
        min-height: 2.25rem !important;
        background: transparent !important;
      }}
      .candidate-combo-unified [data-testid="stSelectbox"] label {{
        display: none !important;
      }}
      .candidate-combo-unified--input-only [data-testid="stTextInput"] input {{
        border-radius: 0 !important;
      }}
      .candidate-combo-unified:focus-within {{
        border-color: rgb(255, 75, 75);
        box-shadow: 0 0 0 1px rgb(255, 75, 75);
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
    `;
    (doc.head || doc.documentElement).appendChild(style);
  }}

  function unifyCombo() {{
    ensureStyles();
    const marker = doc.getElementById(MARKER_ID);
    if (!marker) return;
    const startEc = marker.closest('[data-testid="stElementContainer"]');
    if (!startEc || startEc.dataset.comboUnified === "1") return;

    let textEc = null;
    let selectEc = null;
    let sib = startEc.nextElementSibling;
    while (sib) {{
      if (!textEc && sib.querySelector('[data-testid="stTextInput"]')) {{
        textEc = sib;
      }} else if (textEc && sib.querySelector('[data-testid="stSelectbox"]')) {{
        selectEc = sib;
        break;
      }}
      sib = sib.nextElementSibling;
    }}
    if (!textEc) return;

    const parent = textEc.parentElement;
    if (!parent) return;

    const wrap = doc.createElement("div");
    wrap.className = "candidate-combo-unified";
    if (!selectEc && !EXPECT_PICKER) {{
      wrap.classList.add("candidate-combo-unified--input-only");
    }}
    parent.insertBefore(wrap, textEc);
    wrap.appendChild(textEc);
    if (selectEc) wrap.appendChild(selectEc);
    startEc.dataset.comboUnified = "1";
  }}

  unifyCombo();
  setTimeout(unifyCombo, 80);
  setTimeout(unifyCombo, 300);
  setTimeout(unifyCombo, 800);
  if (doc.body && !doc.body.dataset.comboUnifyObserver) {{
    doc.body.dataset.comboUnifyObserver = "1";
    try {{
      const obs = new MutationObserver(function() {{ unifyCombo(); }});
      obs.observe(doc.body, {{ childList: true, subtree: true }});
    }} catch (e) {{}}
  }}
}})();
</script>
""",
        height=0,
    )

    resolved = resolve_combo_selection(select_key, options, query_key=query_key)
    final = resolved or str(st.session_state.get(select_key) or "").strip()
    if final in options:
        st.markdown(
            f'<div class="candidate-combo-selected-badge">選択中: {final}</div>',
            unsafe_allow_html=True,
        )
        return final
    return ""
