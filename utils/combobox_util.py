"""検索可能コンボボックス（1枠 HTML コンボ、確定時のみ Python へ同期）."""
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


def _combo_query_key(select_key: str, query_key: Optional[str]) -> str:
    return query_key or f"{select_key}_query"


def _parse_combo_component_value(raw: object, *, fallback: str = "") -> tuple[str, str]:
    """(確定値, 入力中ドラフト) を返す."""
    if raw is None:
        return fallback, ""
    raw_s = str(raw).strip()
    if not raw_s:
        return "", ""
    if not raw_s.startswith("{"):
        return raw_s, raw_s
    try:
        payload = json.loads(raw_s)
    except Exception:
        return fallback, ""
    value = str(payload.get("value") or "")
    draft = str(payload.get("draft") or value or "")
    return value, draft


def _apply_committed(
    select_key: str,
    options: List[str],
    value: str,
    *,
    query_key: Optional[str] = None,
) -> str:
    """確定した案件名を session_state に反映する."""
    v = str(value or "").strip()
    if not v or v not in options:
        return ""
    st.session_state[select_key] = v
    st.session_state[f"{select_key}_draft"] = v
    st.session_state[_combo_query_key(select_key, query_key)] = v
    return v


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

    draft = str(st.session_state.get(f"{select_key}_draft") or "").strip()
    if not draft:
        draft = str(st.session_state.get(_combo_query_key(select_key, query_key)) or "").strip()
    if not draft:
        return ""

    for opt in options:
        if opt == draft:
            return _apply_committed(select_key, options, opt, query_key=query_key)

    draft_cf = draft.casefold()
    exact_ci = [opt for opt in options if opt.casefold() == draft_cf]
    if len(exact_ci) == 1:
        return _apply_committed(select_key, options, exact_ci[0], query_key=query_key)

    partial = [opt for opt in options if draft_cf in opt.casefold()]
    if len(partial) == 1:
        return _apply_committed(select_key, options, partial[0], query_key=query_key)

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
    """1枠 HTML コンボボックス。一覧タップ等で確定した値だけ Python にコピーする."""
    del empty_label
    qkey = _combo_query_key(select_key, query_key)

    committed = str(st.session_state.get(select_key) or "").strip()
    if committed and committed not in options:
        committed = ""
        st.session_state[select_key] = ""

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
    committed_js = json.dumps(committed, ensure_ascii=False)
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
  const INITIAL = {committed_js};
  const PLACEHOLDER = {placeholder_js};
  const doc = window.parent && window.parent.document ? window.parent.document : document;

  function anchorEl() {{
    return doc.getElementById(ANCHOR_ID);
  }}

  function wrapEl() {{
    const anchor = anchorEl();
    return anchor ? anchor.querySelector(".candidate-combo-wrap") : null;
  }}

  function emitCommit(value) {{
    const v = String(value || "").trim();
    try {{
      if (window.Streamlit && window.Streamlit.setComponentValue) {{
        window.Streamlit.setComponentValue(JSON.stringify({{
          value: v,
          draft: v
        }}));
      }}
    }} catch (e) {{}}
  }}

  function ensureStyles() {{
    if (doc.getElementById("candidate-combo-styles")) return;
    const style = doc.createElement("style");
    style.id = "candidate-combo-styles";
    style.textContent = `
      .candidate-project-combo-anchor {{
        width: 100%;
        min-height: 40px;
        margin-bottom: 0.25rem;
        position: relative;
        z-index: 1;
      }}
      .candidate-project-combo-anchor.combo-open {{
        z-index: 10050;
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
      .candidate-combo-wrap.combo-open .candidate-combo-input {{
        border-bottom-left-radius: 0;
        border-bottom-right-radius: 0;
        border-bottom-color: rgba(49, 51, 63, 0.12);
      }}
      .candidate-combo-list {{
        position: absolute;
        left: 0;
        right: 0;
        top: calc(100% - 1px);
        z-index: 10001;
        max-height: min(46vh, 260px);
        overflow-y: auto;
        background: #fff;
        border: 1px solid rgba(49, 51, 63, 0.2);
        border-top: none;
        border-radius: 0 0 0.5rem 0.5rem;
        box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12);
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

  function optionsFromDom(wrap) {{
    if (wrap && wrap.dataset.options) {{
      try {{
        return JSON.parse(wrap.dataset.options || "[]");
      }} catch (e) {{}}
    }}
    return OPTIONS;
  }}

  function resolveCommittedFromInput(q, wrap) {{
    const text = (q || "").trim();
    if (!text) return "";
    const opts = optionsFromDom(wrap);
    if (opts.indexOf(text) >= 0) return text;
    const lower = text.toLowerCase();
    const exact = opts.filter(function(o) {{ return String(o).toLowerCase() === lower; }});
    if (exact.length === 1) return exact[0];
    const partial = opts.filter(function(o) {{ return String(o).toLowerCase().indexOf(lower) >= 0; }});
    if (partial.length === 1) return partial[0];
    return "";
  }}

  function commitValue(wrap, input, value) {{
    const v = String(value || "").trim();
    if (!v) return;
    input.value = v;
    wrap.dataset.committed = v;
    emitCommit(v);
  }}

  function syncComboBeforeAction() {{
    const wrap = wrapEl();
    const input = wrap ? wrap.querySelector(".candidate-combo-input") : null;
    if (!input || !wrap) return;
    const draft = (input.value || "").trim();
    if (!draft) {{
      wrap.dataset.committed = "";
      emitCommit("");
      return;
    }}
    const resolved = resolveCommittedFromInput(draft, wrap);
    if (resolved) commitValue(wrap, input, resolved);
  }}

  function bindSearchButtonSync() {{
    if (doc.body && doc.body.dataset.comboSearchSyncBound === "1") return;
    if (doc.body) doc.body.dataset.comboSearchSyncBound = "1";
    const handler = function(ev) {{
      const btn = ev.target && ev.target.closest ? ev.target.closest("button") : null;
      if (!btn) return;
      const label = (btn.textContent || "").replace(/\\s+/g, "");
      if (label.indexOf("検索") < 0) return;
      if (btn.dataset.comboSearchRelease === "1") {{
        btn.dataset.comboSearchRelease = "0";
        return;
      }}
      syncComboBeforeAction();
      if (ev.type === "touchstart" || ev.type === "mousedown") {{
        ev.preventDefault();
        ev.stopPropagation();
        btn.dataset.comboSearchRelease = "1";
        setTimeout(function() {{
          try {{ btn.click(); }} catch (e) {{}}
        }}, 150);
      }}
    }};
    doc.addEventListener("mousedown", handler, true);
    doc.addEventListener("touchstart", handler, {{ capture: true, passive: false }});
  }}

  function bindCombo(wrap, input, list, anchor) {{
    if (wrap.dataset.comboBound === "1") return;
    wrap.dataset.comboBound = "1";
    let committed = INITIAL || "";
    wrap.dataset.committed = committed;
    let activeIndex = -1;

    function filtered() {{
      const opts = optionsFromDom(wrap);
      const q = (input.value || "").trim().toLowerCase();
      if (!q) return opts.slice();
      return opts.filter(function(o) {{
        return String(o).toLowerCase().indexOf(q) >= 0;
      }});
    }}

    function closeList() {{
      wrap.classList.remove("combo-open");
      if (anchor) anchor.classList.remove("combo-open");
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
            committed = opt;
            commitValue(wrap, input, opt);
            closeList();
          }});
          row.addEventListener("touchstart", function(ev) {{
            ev.preventDefault();
            committed = opt;
            commitValue(wrap, input, opt);
            closeList();
          }}, {{ passive: false }});
          list.appendChild(row);
        }});
      }}
      wrap.classList.add("combo-open");
      if (anchor) anchor.classList.add("combo-open");
      list.classList.add("open");
    }}

    function pickActive() {{
      const rows = list.querySelectorAll(".candidate-combo-item");
      if (!rows.length) return;
      if (activeIndex < 0) activeIndex = 0;
      const row = rows[activeIndex];
      if (!row) return;
      const val = row.dataset.value || "";
      committed = val;
      commitValue(wrap, input, val);
      closeList();
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

    input.addEventListener("focus", function() {{ openList(); }});
    input.addEventListener("click", function() {{ openList(); }});
    input.addEventListener("input", function() {{ openList(); }});

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
        const resolved = resolveCommittedFromInput((input.value || "").trim(), wrap);
        if (resolved) {{
          committed = resolved;
          commitValue(wrap, input, resolved);
          closeList();
        }}
        return;
      }}
      if (ev.key === "Escape") {{
        input.value = committed;
        closeList();
      }}
    }});

    input.addEventListener("blur", function() {{
      setTimeout(function() {{
        closeList();
        const q = (input.value || "").trim();
        if (!q) return;
        const resolved = resolveCommittedFromInput(q, wrap);
        if (resolved) {{
          committed = resolved;
          commitValue(wrap, input, resolved);
        }}
      }}, 160);
    }});

    wrap._comboRefresh = function(nextValue, nextOptions) {{
      wrap.dataset.options = JSON.stringify(nextOptions || []);
      wrap.dataset.committed = nextValue || "";
      if (doc.activeElement !== input) {{
        committed = nextValue || "";
        input.value = committed;
      }}
    }};
  }}

  function mount() {{
    ensureStyles();
    const anchor = anchorEl();
    if (!anchor) return false;

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
      bindCombo(wrap, input, list, anchor);
    }}

    wrap.dataset.options = JSON.stringify(OPTIONS);
    if (typeof wrap._comboRefresh === "function") {{
      wrap._comboRefresh(INITIAL, OPTIONS);
    }}
    anchor.dataset.comboReady = "1";
    bindSearchButtonSync();
    return true;
  }}

  function boot() {{
    mount();
    setTimeout(mount, 80);
    setTimeout(mount, 250);
    setTimeout(mount, 700);
    setTimeout(mount, 1500);
  }}

  boot();
  if (doc.body && !doc.body.dataset.comboObserverBound) {{
    doc.body.dataset.comboObserverBound = "1";
    try {{
      const obs = new MutationObserver(function() {{ mount(); }});
      obs.observe(doc.body, {{ childList: true, subtree: true }});
    }} catch (e) {{}}
  }}
}})();
</script>
""",
        height=0,
    )

    chosen, _draft = _parse_combo_component_value(raw, fallback=committed)
    if chosen and chosen in options:
        committed = _apply_committed(select_key, options, chosen, query_key=query_key)

    resolved = resolve_combo_selection(select_key, options, query_key=query_key)
    if resolved:
        committed = resolved

    if help:
        st.caption(help)
    if committed and committed in options:
        st.caption(f"選択中: {committed}")

    return committed if committed in options else ""
