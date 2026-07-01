"""検索可能コンボボックス（1つの text_input + 候補オーバーレイ）."""
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


def _apply_pending_pick(
    select_key: str,
    qkey: str,
    options: List[str],
) -> bool:
    pending = str(st.session_state.pop(f"{select_key}_pending_pick", "") or "").strip()
    if not pending or pending not in options:
        return False
    st.session_state[qkey] = pending
    st.session_state[select_key] = pending
    st.session_state[f"{select_key}_draft"] = pending
    return True


def _sync_selection_from_query(
    select_key: str,
    qkey: str,
    options: List[str],
    query: str,
) -> None:
    q = str(query or "").strip()
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
    """1つの入力欄（Streamlit text_input）+ 候補ドロップダウン（JS オーバーレイ）."""
    del empty_label
    qkey = _combo_query_key(select_key, query_key)
    pending_key = f"{select_key}_pending_pick"
    overlay_id = f"combo_overlay_{''.join(ch if ch.isalnum() else '_' for ch in select_key)}"

    selected = str(st.session_state.get(select_key) or "").strip()
    if selected and selected not in options:
        st.session_state[select_key] = ""
        selected = ""

    if _apply_pending_pick(select_key, qkey, options):
        selected = str(st.session_state.get(select_key) or "").strip()

    if qkey not in st.session_state:
        st.session_state[qkey] = selected

    def _on_query_change() -> None:
        _sync_selection_from_query(
            select_key,
            qkey,
            options,
            str(st.session_state.get(qkey, "") or ""),
        )

    query = st.text_input(
        label,
        key=qkey,
        placeholder=placeholder,
        help=help,
        on_change=_on_query_change,
    )
    q = str(query or "").strip()
    _sync_selection_from_query(select_key, qkey, options, q)

    if not q:
        filtered: List[str] = []
    else:
        filtered = filter_options(options, q)
        if not filtered:
            st.caption("該当する案件がありません。キーワードを変えてください。")

    options_js = json.dumps(options, ensure_ascii=False)
    label_js = json.dumps(label, ensure_ascii=False)
    overlay_id_js = json.dumps(overlay_id)

    raw = components.html(
        f"""
<script src="https://cdn.jsdelivr.net/npm/@streamlit/component-lib@2.0.0/dist/index.min.js"></script>
<script>
(function() {{
  const OPTIONS = {options_js};
  const LABEL = {label_js};
  const OVERLAY_ID = {overlay_id_js};
  const doc = window.parent && window.parent.document ? window.parent.document : document;

  function emitPick(value) {{
    try {{
      if (window.Streamlit && window.Streamlit.setComponentValue) {{
        window.Streamlit.setComponentValue(String(value || ""));
      }}
    }} catch (e) {{}}
  }}

  function ensureStyles() {{
    if (doc.getElementById("candidate-combo-overlay-styles")) return;
    const style = doc.createElement("style");
    style.id = "candidate-combo-overlay-styles";
    style.textContent = `
      .candidate-combo-suggest {{
        position: absolute;
        left: 0;
        right: 0;
        top: calc(100% + 4px);
        z-index: 10001;
        max-height: min(50vh, 280px);
        overflow-y: auto;
        background: #fff;
        border: 1px solid rgba(49, 51, 63, 0.2);
        border-radius: 0.5rem;
        box-shadow: 0 6px 20px rgba(0, 0, 0, 0.14);
        display: none;
      }}
      .candidate-combo-suggest.open {{ display: block; }}
      .candidate-combo-suggest-item {{
        padding: 0.65rem 0.75rem;
        cursor: pointer;
        font-size: 16px;
        color: rgb(49, 51, 63);
        border-bottom: 1px solid rgba(49, 51, 63, 0.06);
        -webkit-tap-highlight-color: rgba(255, 75, 75, 0.12);
      }}
      .candidate-combo-suggest-item:last-child {{ border-bottom: none; }}
      .candidate-combo-suggest-item:hover,
      .candidate-combo-suggest-item.active {{
        background: rgba(255, 75, 75, 0.08);
      }}
      .candidate-combo-suggest-empty {{
        padding: 0.75rem;
        color: rgba(49, 51, 63, 0.55);
        font-size: 0.95rem;
      }}
      .candidate-combo-input-wrap {{
        position: relative;
      }}
    `;
    (doc.head || doc.documentElement).appendChild(style);
  }}

  function findInputForLabel() {{
    const nodes = doc.querySelectorAll(
      '[data-testid="stTextInput"] label, .candidate-combo-label, p'
    );
    for (let i = 0; i < nodes.length; i++) {{
      const node = nodes[i];
      const text = (node.textContent || "").replace(/\\s+/g, "").trim();
      const want = String(LABEL || "").replace(/\\s+/g, "").trim();
      if (!want || text !== want) continue;
      const root =
        node.closest('[data-testid="stTextInput"]') ||
        node.closest('[data-testid="stElementContainer"]');
      if (!root) continue;
      const input = root.querySelector('input[type="text"]');
      if (input) return input;
    }}
    const inputs = doc.querySelectorAll('[data-testid="stTextInput"] input[type="text"]');
    return inputs.length ? inputs[inputs.length - 1] : null;
  }}

  function filteredOptions(q) {{
    const text = (q || "").trim().toLowerCase();
    if (!text) return OPTIONS.slice();
    return OPTIONS.filter(function(o) {{
      return String(o).toLowerCase().indexOf(text) >= 0;
    }});
  }}

  function bindSuggest(input, suggest, list) {{
    if (input.dataset.comboOverlayBound === "1") return;
    input.dataset.comboOverlayBound = "1";
    let activeIndex = -1;

    function closeList() {{
      suggest.classList.remove("open");
    }}

    function openList() {{
      const items = filteredOptions(input.value);
      list.innerHTML = "";
      activeIndex = -1;
      if (!items.length) {{
        const empty = doc.createElement("div");
        empty.className = "candidate-combo-suggest-empty";
        empty.textContent = "該当する案件がありません";
        list.appendChild(empty);
      }} else {{
        items.forEach(function(opt) {{
          const row = doc.createElement("div");
          row.className = "candidate-combo-suggest-item";
          row.textContent = opt;
          row.addEventListener("mousedown", function(ev) {{ ev.preventDefault(); }});
          row.addEventListener("click", function(ev) {{
            ev.preventDefault();
            input.value = opt;
            closeList();
            emitPick(opt);
          }});
          list.appendChild(row);
        }});
      }}
      suggest.classList.add("open");
    }}

    input.addEventListener("focus", function() {{ openList(); }});
    input.addEventListener("input", function() {{ openList(); }});
    input.addEventListener("keydown", function(ev) {{
      const rows = list.querySelectorAll(".candidate-combo-suggest-item");
      if (ev.key === "ArrowDown") {{
        ev.preventDefault();
        if (!suggest.classList.contains("open")) openList();
        if (!rows.length) return;
        activeIndex = Math.min(rows.length - 1, activeIndex + 1);
        rows.forEach(function(r, i) {{ r.classList.toggle("active", i === activeIndex); }});
        return;
      }}
      if (ev.key === "ArrowUp") {{
        ev.preventDefault();
        if (!rows.length) return;
        activeIndex = Math.max(0, activeIndex - 1);
        rows.forEach(function(r, i) {{ r.classList.toggle("active", i === activeIndex); }});
        return;
      }}
      if (ev.key === "Enter" && suggest.classList.contains("open") && rows.length) {{
        ev.preventDefault();
        if (activeIndex < 0) activeIndex = 0;
        const row = rows[activeIndex];
        if (!row) return;
        const val = (row.textContent || "").trim();
        if (!val) return;
        input.value = val;
        closeList();
        emitPick(val);
        return;
      }}
      if (ev.key === "Escape") {{
        closeList();
      }}
    }});
    input.addEventListener("blur", function() {{
      setTimeout(closeList, 160);
    }});
  }}

  function mount() {{
    ensureStyles();
    const input = findInputForLabel();
    if (!input) return;

    const textRoot =
      input.closest('[data-testid="stTextInput"]') ||
      input.closest('[data-testid="stElementContainer"]');
    if (!textRoot) return;

    let wrap = textRoot.querySelector(".candidate-combo-input-wrap");
    if (!wrap) {{
      wrap = doc.createElement("div");
      wrap.className = "candidate-combo-input-wrap";
      const parent = input.parentElement;
      if (!parent) return;
      parent.insertBefore(wrap, input);
      wrap.appendChild(input);
    }}

    let suggest = wrap.querySelector(".candidate-combo-suggest");
    if (!suggest) {{
      suggest = doc.createElement("div");
      suggest.className = "candidate-combo-suggest";
      suggest.id = OVERLAY_ID;
      const list = doc.createElement("div");
      suggest.appendChild(list);
      wrap.appendChild(suggest);
      bindSuggest(input, suggest, list);
    }}
  }}

  mount();
  setTimeout(mount, 120);
  setTimeout(mount, 500);
  setTimeout(mount, 1200);
}})();
</script>
""",
        height=0,
    )

    picked = str(raw or "").strip()
    if picked and picked in options:
        if str(st.session_state.get(qkey) or "").strip() != picked:
            st.session_state[pending_key] = picked
            st.rerun()

    resolved = resolve_combo_selection(select_key, options, query_key=query_key)
    if resolved:
        return resolved

    final = str(st.session_state.get(select_key) or "").strip()
    return final if final in options else ""
