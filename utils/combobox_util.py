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
    marker_id = f"combo_marker_{''.join(ch if ch.isalnum() else '_' for ch in select_key)}"

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

    st.markdown(
        f'<div id="{marker_id}" data-candidate-combo-marker="1" style="display:none;height:0;margin:0;padding:0;"></div>',
        unsafe_allow_html=True,
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

    options_js = json.dumps(options, ensure_ascii=False)
    marker_id_js = json.dumps(marker_id)
    overlay_id_js = json.dumps(overlay_id)

    raw = components.html(
        f"""
<script src="https://cdn.jsdelivr.net/npm/@streamlit/component-lib@2.0.0/dist/index.min.js"></script>
<script>
(function() {{
  const frame = window.frameElement;
  if (frame) {{
    frame.style.setProperty("height", "0px", "important");
    frame.style.setProperty("min-height", "0px", "important");
    frame.style.setProperty("border", "none", "important");
    const frameWrap = frame.parentElement;
    if (frameWrap) {{
      frameWrap.style.setProperty("margin", "0", "important");
      frameWrap.style.setProperty("padding", "0", "important");
    }}
  }}

  const OPTIONS = {options_js};
  const MARKER_ID = {marker_id_js};
  const OVERLAY_ID = {overlay_id_js};
  const doc = window.parent && window.parent.document ? window.parent.document : document;

  function isComboInput(el) {{
    if (!el || el.tagName !== "INPUT") return false;
    const t = (el.getAttribute("type") || "text").toLowerCase();
    return t === "text" || t === "search" || t === "";
  }}

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
      .candidate-combo-host {{
        position: relative !important;
      }}
      [data-testid="stElementContainer"].combo-element-open {{
        position: relative;
        z-index: 10050;
      }}
      .candidate-combo-input-mount {{
        position: relative !important;
      }}
      .candidate-combo-input-mount.combo-open input {{
        border-bottom-left-radius: 0 !important;
        border-bottom-right-radius: 0 !important;
        border-bottom-color: rgba(49, 51, 63, 0.12) !important;
      }}
      .candidate-combo-host input {{
        border: 1px solid rgba(49, 51, 63, 0.2) !important;
        border-radius: 0.5rem !important;
        font-size: 16px !important;
        box-shadow: none !important;
        background: #fff !important;
      }}
      .candidate-combo-host input:focus {{
        border-color: rgb(255, 75, 75) !important;
        box-shadow: 0 0 0 1px rgb(255, 75, 75) !important;
        outline: none !important;
      }}
      .candidate-combo-suggest {{
        position: absolute;
        left: 0;
        right: 0;
        top: 100%;
        margin-top: -1px;
        z-index: 10001;
        max-height: min(46vh, 260px);
        overflow-y: auto;
        overflow-x: hidden;
        background: #fff;
        border: 1px solid rgba(49, 51, 63, 0.2);
        border-top: none;
        border-radius: 0 0 0.5rem 0.5rem;
        box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12);
        display: none;
      }}
      .candidate-combo-suggest.open {{
        display: block !important;
      }}
      .candidate-combo-suggest-item {{
        padding: 0.65rem 0.75rem;
        cursor: pointer;
        font-size: 16px;
        color: rgb(49, 51, 63);
        border-bottom: 1px solid rgba(49, 51, 63, 0.06);
        -webkit-tap-highlight-color: rgba(255, 75, 75, 0.12);
        line-height: 1.35;
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
    `;
    (doc.head || doc.documentElement).appendChild(style);
  }}

  function findInputByMarker() {{
    const marker = doc.getElementById(MARKER_ID);
    if (!marker) return null;
    const markerEc = marker.closest('[data-testid="stElementContainer"]');
    if (!markerEc) return null;
    let sibling = markerEc.nextElementSibling;
    while (sibling) {{
      const inputs = sibling.querySelectorAll("input");
      for (let i = 0; i < inputs.length; i++) {{
        if (isComboInput(inputs[i])) return inputs[i];
      }}
      sibling = sibling.nextElementSibling;
    }}
    const block = markerEc.closest('[data-testid="stVerticalBlock"], [data-testid="stForm"]');
    if (!block) return null;
    let seen = false;
    const containers = block.querySelectorAll('[data-testid="stElementContainer"]');
    for (let i = 0; i < containers.length; i++) {{
      const ec = containers[i];
      if (ec === markerEc) {{
        seen = true;
        continue;
      }}
      if (!seen) continue;
      const inputs = ec.querySelectorAll("input");
      for (let j = 0; j < inputs.length; j++) {{
        if (isComboInput(inputs[j])) return inputs[j];
      }}
    }}
    return null;
  }}

  function filteredOptions(q) {{
    const text = (q || "").trim().toLowerCase();
    if (!text) return OPTIONS.slice();
    return OPTIONS.filter(function(o) {{
      return String(o).toLowerCase().indexOf(text) >= 0;
    }});
  }}

  function bindSuggest(mountPoint, elementHost, input, suggest, list) {{
    const inputId = input.getAttribute("data-combo-input-id") || String(Date.now()) + Math.random();
    input.setAttribute("data-combo-input-id", inputId);
    if (mountPoint.dataset.comboBoundInputId === inputId) return;
    mountPoint.dataset.comboBoundInputId = inputId;
    let activeIndex = -1;

    function closeList() {{
      mountPoint.classList.remove("combo-open");
      if (elementHost) elementHost.classList.remove("combo-element-open");
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
          row.addEventListener("touchstart", function(ev) {{ ev.preventDefault(); }}, {{ passive: false }});
          row.addEventListener("click", function(ev) {{
            ev.preventDefault();
            input.value = opt;
            closeList();
            emitPick(opt);
          }});
          list.appendChild(row);
        }});
      }}
      mountPoint.classList.add("combo-open");
      if (elementHost) elementHost.classList.add("combo-element-open");
      suggest.classList.add("open");
    }}

    input.addEventListener("focus", function() {{ openList(); }});
    input.addEventListener("click", function() {{ openList(); }});
    input.addEventListener("touchstart", function() {{
      setTimeout(openList, 0);
    }}, {{ passive: true }});
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

  function findMountPoint(input, host) {{
    let anchor = input.parentElement;
    while (anchor && anchor !== host) {{
      const parent = anchor.parentElement;
      if (!parent || parent === host) break;
      const inputs = parent.querySelectorAll("input");
      if (inputs.length > 1) break;
      anchor = parent;
    }}
    return anchor || input.parentElement || host;
  }}

  function mount() {{
    ensureStyles();
    const input = findInputByMarker();
    if (!input) return false;

    const host = input.closest('[data-testid="stTextInput"]') || input.closest('[data-testid="stElementContainer"]');
    if (!host) return false;
    host.classList.add("candidate-combo-host");
    const elementHost = host.closest('[data-testid="stElementContainer"]');
    const mountPoint = findMountPoint(input, host);

    mountPoint.classList.add("candidate-combo-input-mount");
    let suggest = mountPoint.querySelector(".candidate-combo-suggest");
    let list;
    if (!suggest) {{
      suggest = doc.createElement("div");
      suggest.className = "candidate-combo-suggest";
      suggest.id = OVERLAY_ID;
      list = doc.createElement("div");
      suggest.appendChild(list);
      mountPoint.appendChild(suggest);
    }} else {{
      list = suggest.firstElementChild;
    }}
    if (!list) {{
      list = doc.createElement("div");
      suggest.appendChild(list);
    }}
    bindSuggest(mountPoint, elementHost, input, suggest, list);
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
