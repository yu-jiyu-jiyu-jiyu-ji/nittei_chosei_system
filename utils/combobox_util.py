"""検索可能コンボボックス（スマホでキーボードが出るテキスト入力型）."""
from __future__ import annotations

import html
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


def _parse_combo_component_value(raw: object, *, fallback: str = "") -> str:
    if raw is None:
        return fallback
    raw_s = str(raw).strip()
    if not raw_s:
        return ""
    if not raw_s.startswith("{"):
        return raw_s
    try:
        payload = json.loads(raw_s)
    except Exception:
        return fallback
    return str(payload.get("value") or "")


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
    """親ページにマウントする1枠コンボボックス（スマホでキーボード表示）."""
    del query_key, empty_label  # 互換のため引数のみ残す

    current = str(st.session_state.get(select_key) or "")
    if current and current not in options:
        current = ""
        st.session_state[select_key] = ""
    st.session_state.pop(f"{select_key}_query", None)

    widget_id = "".join(ch if ch.isalnum() else "_" for ch in select_key)
    anchor_id = f"combo_anchor_{widget_id}"
    input_id = f"combo_input_{widget_id}"
    list_id = f"combo_list_{widget_id}"

    st.markdown(
        f'<p class="candidate-combo-label">{html.escape(label)}</p>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<div id="{anchor_id}" class="candidate-project-combo-anchor"></div>',
        unsafe_allow_html=True,
    )

    options_js = json.dumps(options, ensure_ascii=False)
    current_js = json.dumps(current, ensure_ascii=False)
    placeholder_js = json.dumps(placeholder, ensure_ascii=False)

    raw = components.html(
        f"""
<script src="https://cdn.jsdelivr.net/npm/@streamlit/component-lib@2.0.0/dist/index.min.js"></script>
<script>
(function() {{
  const ANCHOR_ID = {json.dumps(anchor_id)};
  const INPUT_ID = {json.dumps(input_id)};
  const LIST_ID = {json.dumps(list_id)};
  const OPTIONS = {options_js};
  const INITIAL = {current_js};
  const PLACEHOLDER = {placeholder_js};
  const doc = window.parent && window.parent.document ? window.parent.document : document;

  function ensureStyles() {{
    if (doc.getElementById("candidate-combo-styles")) return;
    const style = doc.createElement("style");
    style.id = "candidate-combo-styles";
    style.textContent = `
      .candidate-project-combo-anchor {{
        width: 100%;
        margin-bottom: 0.25rem;
      }}
      .candidate-combo-label {{
        font-size: 0.875rem;
        font-weight: 600;
        color: rgb(49, 51, 63);
        margin: 0 0 0.35rem 0;
      }}
      .candidate-combo-wrap {{
        width: 100%;
        position: relative;
        box-sizing: border-box;
      }}
      .candidate-combo-input {{
        width: 100%;
        box-sizing: border-box;
        height: 40px;
        padding: 0.5rem 0.75rem;
        border: 1px solid rgba(49, 51, 63, 0.2);
        border-radius: 0.5rem;
        font-size: 16px;
        color: rgb(49, 51, 63);
        background: #fff;
        outline: none;
        -webkit-appearance: none;
      }}
      .candidate-combo-input:focus {{
        border-color: rgb(255, 75, 75);
        box-shadow: 0 0 0 1px rgb(255, 75, 75);
      }}
      .candidate-combo-list {{
        position: absolute;
        left: 0;
        right: 0;
        top: calc(100% + 4px);
        z-index: 9999;
        max-height: min(50vh, 280px);
        overflow-y: auto;
        background: #fff;
        border: 1px solid rgba(49, 51, 63, 0.2);
        border-radius: 0.5rem;
        box-shadow: 0 6px 20px rgba(0, 0, 0, 0.14);
        display: none;
      }}
      .candidate-combo-list.open {{ display: block; }}
      .candidate-combo-item {{
        padding: 0.65rem 0.75rem;
        cursor: pointer;
        font-size: 16px;
        color: rgb(49, 51, 63);
        border-bottom: 1px solid rgba(49, 51, 63, 0.06);
        -webkit-tap-highlight-color: rgba(255, 75, 75, 0.12);
      }}
      .candidate-combo-item:last-child {{ border-bottom: none; }}
      .candidate-combo-item:hover,
      .candidate-combo-item.active {{
        background: rgba(255, 75, 75, 0.08);
      }}
      .candidate-combo-empty {{
        padding: 0.75rem;
        color: rgba(49, 51, 63, 0.55);
        font-size: 0.95rem;
      }}
    `;
    (doc.head || doc.documentElement).appendChild(style);
  }}

  function emit(value) {{
    try {{
      if (window.Streamlit && window.Streamlit.setComponentValue) {{
        window.Streamlit.setComponentValue(JSON.stringify({{ value: value || "" }}));
      }}
    }} catch (e) {{}}
  }}

  function bindCombo(wrap, input, list) {{
    if (wrap.dataset.comboBound === "1") return;
    wrap.dataset.comboBound = "1";
    let committed = INITIAL || "";
    let activeIndex = -1;
    let suppressEmit = false;

    function optionsNow() {{
      try {{
        return JSON.parse(wrap.dataset.options || "[]");
      }} catch (e) {{
        return [];
      }}
    }}

    function filtered() {{
      const opts = optionsNow();
      const q = (input.value || "").trim().toLowerCase();
      if (!q) return opts.slice();
      return opts.filter(function(o) {{
        return String(o).toLowerCase().indexOf(q) >= 0;
      }});
    }}

    function closeList() {{
      list.classList.remove("open");
    }}

    function openList() {{
      const items = filtered();
      list.innerHTML = "";
      activeIndex = -1;
      if (!items.length) {{
        const empty = doc.createElement("div");
        empty.className = "candidate-combo-empty";
        empty.textContent = "該当する案件がありません";
        list.appendChild(empty);
      }} else {{
        items.forEach(function(opt) {{
          const row = doc.createElement("div");
          row.className = "candidate-combo-item";
          row.textContent = opt;
          row.dataset.value = opt;
          row.addEventListener("mousedown", function(ev) {{
            ev.preventDefault();
          }});
          row.addEventListener("click", function(ev) {{
            ev.preventDefault();
            input.value = opt;
            committed = opt;
            closeList();
            emit(opt);
          }});
          list.appendChild(row);
        }});
      }}
      list.classList.add("open");
    }}

    function pickActive() {{
      const rows = list.querySelectorAll(".candidate-combo-item");
      if (!rows.length) return;
      if (activeIndex < 0) activeIndex = 0;
      const row = rows[activeIndex];
      if (!row) return;
      const val = row.dataset.value || "";
      input.value = val;
      committed = val;
      closeList();
      emit(val);
    }}

    input.placeholder = PLACEHOLDER;
    input.value = committed;
    input.setAttribute("inputmode", "search");
    input.setAttribute("enterkeyhint", "search");
    input.setAttribute("autocomplete", "off");
    input.setAttribute("autocorrect", "off");
    input.setAttribute("autocapitalize", "off");
    input.setAttribute("spellcheck", "false");

    input.addEventListener("touchstart", function() {{
      try {{ input.focus({{ preventScroll: false }}); }} catch (e) {{ input.focus(); }}
    }}, {{ passive: true }});

    input.addEventListener("focus", function() {{
      openList();
    }});

    input.addEventListener("input", function() {{
      openList();
    }});

    input.addEventListener("keydown", function(ev) {{
      const rows = list.querySelectorAll(".candidate-combo-item");
      if (ev.key === "ArrowDown") {{
        ev.preventDefault();
        if (!list.classList.contains("open")) openList();
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
      if (ev.key === "Enter") {{
        ev.preventDefault();
        if (list.classList.contains("open") && rows.length) {{
          pickActive();
          return;
        }}
        const q = (input.value || "").trim();
        const opts = optionsNow();
        if (opts.indexOf(q) >= 0) {{
          committed = q;
          emit(q);
        }}
        return;
      }}
      if (ev.key === "Escape") {{
        suppressEmit = true;
        input.value = committed;
        suppressEmit = false;
        closeList();
      }}
    }});

    input.addEventListener("blur", function() {{
      setTimeout(function() {{
        closeList();
        const q = (input.value || "").trim();
        const opts = optionsNow();
        if (!q) {{
          if (committed) {{
            committed = "";
            emit("");
          }}
          return;
        }}
        if (opts.indexOf(q) >= 0) {{
          if (q !== committed) {{
            committed = q;
            emit(q);
          }}
          return;
        }}
        suppressEmit = true;
        input.value = committed;
        suppressEmit = false;
      }}, 160);
    }});

    wrap._comboRefresh = function(nextValue, nextOptions) {{
      wrap.dataset.options = JSON.stringify(nextOptions || []);
      if (doc.activeElement !== input) {{
        committed = nextValue || "";
        input.value = committed;
      }}
    }};
  }}

  function mount() {{
    ensureStyles();
    const anchor = doc.getElementById(ANCHOR_ID);
    if (!anchor) return;

    let wrap = anchor.querySelector(".candidate-combo-wrap");
    if (!wrap) {{
      wrap = doc.createElement("div");
      wrap.className = "candidate-combo-wrap";
      wrap.dataset.options = JSON.stringify(OPTIONS);
      const input = doc.createElement("input");
      input.type = "text";
      input.className = "candidate-combo-input";
      input.id = INPUT_ID;
      const list = doc.createElement("div");
      list.className = "candidate-combo-list";
      list.id = LIST_ID;
      wrap.appendChild(input);
      wrap.appendChild(list);
      anchor.appendChild(wrap);
      bindCombo(wrap, input, list);
    }}

    wrap.dataset.options = JSON.stringify(OPTIONS);
    if (typeof wrap._comboRefresh === "function") {{
      wrap._comboRefresh(INITIAL, OPTIONS);
    }}
    anchor.dataset.comboReady = "1";
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

    chosen = _parse_combo_component_value(raw, fallback=current)
    if chosen != current:
        if chosen and chosen not in options:
            chosen = ""
        st.session_state[select_key] = chosen
        current = chosen

    if help:
        st.caption(help)

    return current if current in options else ""
