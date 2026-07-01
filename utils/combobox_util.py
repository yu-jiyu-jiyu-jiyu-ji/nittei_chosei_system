"""検索可能コンボボックス（1つの入力欄に統合）."""
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
    """1つのコンボボックス（入力で絞り込み、一覧から選択）."""
    del query_key  # 旧2段UI用。互換のため引数のみ残す。

    current = str(st.session_state.get(select_key, "") or "")
    if current and current not in options:
        current = ""
        st.session_state[select_key] = ""

    options_js = json.dumps(options, ensure_ascii=False)
    current_js = json.dumps(current, ensure_ascii=False)
    placeholder_js = json.dumps(placeholder, ensure_ascii=False)
    label_js = json.dumps(label, ensure_ascii=False)
    empty_label_js = json.dumps(empty_label, ensure_ascii=False)
    widget_id = "".join(ch if ch.isalnum() else "_" for ch in select_key)

    raw = components.html(
        f"""
<script src="https://cdn.jsdelivr.net/npm/@streamlit/component-lib@2.0.0/dist/index.min.js"></script>
<style>
  html, body {{
    margin: 0;
    padding: 0;
    font-family: "Source Sans Pro", sans-serif;
  }}
  .st-combo-wrap {{
    width: 100%;
    box-sizing: border-box;
    position: relative;
  }}
  .st-combo-label {{
    font-size: 0.875rem;
    font-weight: 600;
    color: rgb(49, 51, 63);
    margin: 0 0 0.35rem 0;
  }}
  .st-combo-input {{
    width: 100%;
    box-sizing: border-box;
    height: 38px;
    padding: 0.45rem 0.75rem;
    border: 1px solid rgba(49, 51, 63, 0.2);
    border-radius: 0.5rem;
    font-size: 1rem;
    color: rgb(49, 51, 63);
    background: #fff;
    outline: none;
  }}
  .st-combo-input:focus {{
    border-color: rgb(255, 75, 75);
    box-shadow: 0 0 0 1px rgb(255, 75, 75);
  }}
  .st-combo-list {{
    position: absolute;
    left: 0;
    right: 0;
    top: calc(100% + 4px);
    z-index: 1000;
    max-height: 240px;
    overflow-y: auto;
    background: #fff;
    border: 1px solid rgba(49, 51, 63, 0.2);
    border-radius: 0.5rem;
    box-shadow: 0 4px 16px rgba(0, 0, 0, 0.12);
    display: none;
  }}
  .st-combo-list.open {{
    display: block;
  }}
  .st-combo-item {{
    padding: 0.55rem 0.75rem;
    cursor: pointer;
    font-size: 0.95rem;
    color: rgb(49, 51, 63);
    border-bottom: 1px solid rgba(49, 51, 63, 0.06);
  }}
  .st-combo-item:last-child {{
    border-bottom: none;
  }}
  .st-combo-item:hover,
  .st-combo-item.active {{
    background: rgba(255, 75, 75, 0.08);
  }}
  .st-combo-empty {{
    padding: 0.65rem 0.75rem;
    color: rgba(49, 51, 63, 0.55);
    font-size: 0.9rem;
  }}
</style>
<div class="st-combo-wrap" id="combo-{widget_id}">
  <div class="st-combo-label">{label}</div>
  <input
    type="text"
    class="st-combo-input"
    id="combo-input-{widget_id}"
    autocomplete="off"
    spellcheck="false"
  />
  <div class="st-combo-list" id="combo-list-{widget_id}"></div>
</div>
<script>
(function() {{
  const OPTIONS = {options_js};
  const INITIAL = {current_js};
  const PLACEHOLDER = {placeholder_js};
  const EMPTY_LABEL = {empty_label_js};
  const input = document.getElementById("combo-input-{widget_id}");
  const list = document.getElementById("combo-list-{widget_id}");
  if (!input || !list) return;

  let committed = INITIAL || "";
  let activeIndex = -1;
  let suppressEmit = false;

  function setFrameHeight() {{
    try {{
      const open = list.classList.contains("open");
      const h = open
        ? Math.min(320, 72 + Math.min(OPTIONS.length, 6) * 38)
        : 72;
      if (window.Streamlit && window.Streamlit.setFrameHeight) {{
        window.Streamlit.setFrameHeight(h);
      }}
    }} catch (e) {{}}
  }}

  function emit(value) {{
    if (suppressEmit) return;
    committed = value || "";
    try {{
      if (window.Streamlit && window.Streamlit.setComponentValue) {{
        window.Streamlit.setComponentValue(JSON.stringify({{ value: committed }}));
      }}
    }} catch (e) {{}}
  }}

  function filtered() {{
    const q = (input.value || "").trim().toLowerCase();
    if (!q) return OPTIONS.slice();
    return OPTIONS.filter(function(o) {{
      return String(o).toLowerCase().indexOf(q) >= 0;
    }});
  }}

  function renderList() {{
    const items = filtered();
    list.innerHTML = "";
    activeIndex = -1;
    if (!items.length) {{
      const empty = document.createElement("div");
      empty.className = "st-combo-empty";
      empty.textContent = "該当する案件がありません";
      list.appendChild(empty);
      list.classList.add("open");
      setFrameHeight();
      return;
    }}
    items.forEach(function(opt, idx) {{
      const row = document.createElement("div");
      row.className = "st-combo-item";
      row.textContent = opt;
      row.dataset.value = opt;
      row.addEventListener("mousedown", function(ev) {{
        ev.preventDefault();
        input.value = opt;
        list.classList.remove("open");
        emit(opt);
        setFrameHeight();
      }});
      list.appendChild(row);
    }});
    list.classList.add("open");
    setFrameHeight();
  }}

  function pickActive() {{
    const rows = list.querySelectorAll(".st-combo-item");
    if (!rows.length) return;
    if (activeIndex < 0) activeIndex = 0;
    const row = rows[activeIndex];
    if (!row) return;
    const val = row.dataset.value || "";
    input.value = val;
    list.classList.remove("open");
    emit(val);
    setFrameHeight();
  }}

  input.placeholder = PLACEHOLDER;
  input.value = committed;
  setFrameHeight();

  input.addEventListener("focus", function() {{
    renderList();
  }});

  input.addEventListener("input", function() {{
    renderList();
  }});

  input.addEventListener("keydown", function(ev) {{
    const rows = list.querySelectorAll(".st-combo-item");
    if (ev.key === "ArrowDown") {{
      ev.preventDefault();
      if (!list.classList.contains("open")) renderList();
      if (!rows.length) return;
      activeIndex = Math.min(rows.length - 1, activeIndex + 1);
      rows.forEach(function(r, i) {{ r.classList.toggle("active", i === activeIndex); }});
      if (rows[activeIndex]) rows[activeIndex].scrollIntoView({{ block: "nearest" }});
      return;
    }}
    if (ev.key === "ArrowUp") {{
      ev.preventDefault();
      if (!rows.length) return;
      activeIndex = Math.max(0, activeIndex - 1);
      rows.forEach(function(r, i) {{ r.classList.toggle("active", i === activeIndex); }});
      if (rows[activeIndex]) rows[activeIndex].scrollIntoView({{ block: "nearest" }});
      return;
    }}
    if (ev.key === "Enter") {{
      ev.preventDefault();
      if (list.classList.contains("open") && rows.length) {{
        pickActive();
        return;
      }}
      const q = (input.value || "").trim();
      if (OPTIONS.indexOf(q) >= 0) {{
        emit(q);
      }}
      return;
    }}
    if (ev.key === "Escape") {{
      suppressEmit = true;
      input.value = committed;
      suppressEmit = false;
      list.classList.remove("open");
      setFrameHeight();
    }}
  }});

  input.addEventListener("blur", function() {{
    setTimeout(function() {{
      list.classList.remove("open");
      setFrameHeight();
      const q = (input.value || "").trim();
      if (!q) {{
        if (committed) emit("");
        else input.value = "";
        return;
      }}
      if (OPTIONS.indexOf(q) >= 0) {{
        if (q !== committed) emit(q);
        return;
      }}
      suppressEmit = true;
      input.value = committed;
      suppressEmit = false;
    }}, 120);
  }});

  document.addEventListener("click", function(ev) {{
    if (!ev.target.closest("#combo-{widget_id}")) {{
      list.classList.remove("open");
      setFrameHeight();
    }}
  }});
}})();
</script>
""",
        height=72,
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
