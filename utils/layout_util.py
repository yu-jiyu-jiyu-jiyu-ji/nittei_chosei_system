"""レイアウト統一ユーティリティ."""

from __future__ import annotations

import json

import streamlit as st
import streamlit.components.v1 as components

# Streamlit 右上メニュー（Get help / Report a bug / About）を非表示にする
STREAMLIT_MENU_ITEMS = {
    "Get help": None,
    "Report a bug": None,
    "About": None,
}

_NAV_PAGES = [
    ("pages/01_案件一覧.py", "案件一覧", "📋"),
    ("pages/03_候補検索.py", "候補検索", "🔍"),
    ("pages/04_共通設定.py", "共通設定", "⚙️"),
    ("pages/06_問い合わせ履歴.py", "問い合わせ履歴", "💬"),
]
_NAV_ADMIN_PAGES = [
    ("pages/07_問い合わせ管理.py", "問い合わせ管理", "📨"),
]

_SIDEBAR_NAV_CSS = """<style>
[data-testid="stSidebar"] div.nav-links .stButton > button {
    background: transparent; border: none; color: inherit;
    text-align: left; padding: 0.4rem 0.75rem; border-radius: 0.5rem;
    font-size: 0.875rem; cursor: pointer; width: 100%;
}
[data-testid="stSidebar"] div.nav-links .stButton > button:hover {
    background: rgba(151,166,195,0.18);
}
</style>"""


def inject_sidebar_nav() -> None:
    """サイドバーにページナビを注入（config.toml の showSidebarNavigation=false 用）.

    st.sidebar.button + st.switch_page を使い、遷移前に session_state で
    サイドバー折りたたみフラグを立てる。page_link と違い Python 側で
    遷移を制御できるため、遷移先の初回描画でサイドバーを閉じられる。
    """
    st.sidebar.markdown(_SIDEBAR_NAV_CSS, unsafe_allow_html=True)
    st.sidebar.markdown("### メニュー")
    pages = list(_NAV_PAGES)
    if st.session_state.get("current_user_role") == "admin":
        pages.extend(_NAV_ADMIN_PAGES)
    container = st.sidebar.container()
    container.markdown('<div class="nav-links">', unsafe_allow_html=True)
    for _path, _label, _icon in pages:
        if container.button(
            f"{_icon}　{_label}",
            key=f"_nav_{_path}",
            use_container_width=True,
        ):
            st.session_state["_sidebar_collapse"] = True
            st.switch_page(_path)
    container.markdown("</div>", unsafe_allow_html=True)


def inject_floating_inquiry_button() -> None:
    """全画面右下の「?」ボタン。押下で問い合わせダイアログ（st.dialog）を開く."""
    from utils.inquiry_dialog_util import render_inquiry_floating_button

    render_inquiry_floating_button()


def _inject_select_toggle_fix() -> None:
    """プルダウンをクリックで開閉トグルできるようにする JS パッチ.

    メニュー展開後はフォーカスが input 外に出るため、aria-expanded と
    表示中の listbox 付き popover で「開いている」を判定する。
    """
    components.html(
        """
        <script>
        (function() {
            var doc = window.parent.document;
            if (doc._stPullDownToggleFix) return;
            doc._stPullDownToggleFix = true;

            function basewebSelectMenuOpen(select) {
                if (!select) return false;
                if (select.getAttribute('aria-expanded') === 'true') return true;
                if (select.querySelector('[aria-expanded="true"]')) return true;
                var pops = doc.querySelectorAll('[data-baseweb="popover"]');
                for (var i = 0; i < pops.length; i++) {
                    var p = pops[i];
                    if (!p.querySelector('[role="listbox"]')) continue;
                    var st = window.getComputedStyle(p);
                    if (st.display === 'none' || st.visibility === 'hidden') continue;
                    if (parseFloat(st.opacity || '1') < 0.01) continue;
                    var r = p.getBoundingClientRect();
                    if (r.width < 2 || r.height < 2) continue;
                    return true;
                }
                return false;
            }

            function onPress(e) {
                var select = e.target.closest('[data-baseweb="select"]');
                if (!select) return;
                if (e.target.closest('[role="listbox"]')
                    || e.target.closest('[role="option"]')
                    || e.target.closest('[data-baseweb="popover"]')
                    || e.target.closest('[data-baseweb="tag"]')) return;

                var input = select.querySelector('input');
                var menuOpen = basewebSelectMenuOpen(select);
                var inputFocused = input && doc.activeElement === input;

                if (menuOpen || inputFocused) {
                    e.preventDefault();
                    e.stopPropagation();
                    var ae = doc.activeElement;
                    if (ae && typeof ae.blur === 'function') ae.blur();
                    if (input && typeof input.blur === 'function') input.blur();
                    var esc = new KeyboardEvent('keydown', {
                        key: 'Escape',
                        code: 'Escape',
                        keyCode: 27,
                        which: 27,
                        bubbles: true,
                        cancelable: true,
                    });
                    doc.body.dispatchEvent(esc);
                    select.dispatchEvent(esc);
                }
            }

            doc.addEventListener('pointerdown', onPress, true);
        })();
        </script>
        """,
        height=0,
    )


def _inject_sidebar_collapse_js() -> None:
    """サイドバー折りたたみボタンをクリックして Streamlit 内部状態を更新する JS."""
    components.html(
        """
        <script>
        (function() {
            var doc = window.parent.document;
            function collapse() {
                var btn =
                    doc.querySelector('[data-testid="stSidebarCollapseButton"] button') ||
                    doc.querySelector('[data-testid="stSidebarCollapseButton"]') ||
                    doc.querySelector('[data-testid="stSidebar"] [data-testid="stBaseButton-headerNoPadding"]') ||
                    doc.querySelector('[data-testid="stSidebar"] header button');
                /* aria-expanded の直接書き換えは React 状態と不整合になり、開く「>>」が消えることがある */
                if (btn) { btn.click(); return true; }
                return false;
            }
            var n = 0;
            var maxTries = 45;
            var delayMs = 80;
            (function retry() {
                if (collapse() || ++n > maxTries) return;
                setTimeout(retry, delayMs);
            })();
        })();
        </script>
        """,
        height=0,
    )


def _inject_page_busy_reset(page_id: str) -> None:
    """ページ遷移のたびに強制マーカーとオーバーレイを解除（HTML をページごとに変えて再実行）."""
    pid_js = json.dumps(page_id or "app")
    components.html(
        f"<!-- st-page-reset:{page_id or 'app'} -->\n"
        f"""
        <script>
        (function() {{
            var doc = window.parent.document;
            var pageId = {pid_js};
            doc.body.setAttribute("data-st-active-page-id", pageId);
            doc.querySelectorAll("#_st_force_busy_marker, ._st_force_busy_marker").forEach(function(el) {{
                el.remove();
            }});
            var S = doc._stGlobalBusyOverlay;
            if (S) {{
                if (S.hideSpinTimer) {{ clearTimeout(S.hideSpinTimer); S.hideSpinTimer = null; }}
                if (S.pendingTimer) {{ clearTimeout(S.pendingTimer); S.pendingTimer = null; }}
                S.state = "idle";
                S._candidateForceTitle = null;
            }}
            var L = doc.getElementById("_st_global_busy_layer");
            if (L) L.classList.remove("_st_busy_on", "_st_busy_pending");
        }})();
        </script>
        """,
        height=0,
    )


def _inject_global_busy_overlay() -> None:
    """stSpinner 表示中・および再実行直後に全画面の読み込みレイヤーを重ねる."""
    components.html(
        """
        <script>
        (function() {
            var doc = window.parent.document;
            var SPIN_HIDE_MS = 480;
            var PENDING_MAX_MS = 2200;
            var MO_DEBOUNCE_MS = 40;

            function ensureStyle() {
                if (doc.getElementById("_st_global_busy_layer_style")) return;
                var css = "#_st_global_busy_layer{position:fixed;inset:0;z-index:999900;display:flex;"
                    + "align-items:center;justify-content:center;pointer-events:none;opacity:0;"
                    + "transition:opacity 0.12s ease-out;}"
                    + "#_st_global_busy_layer._st_busy_on{opacity:1;pointer-events:auto;}"
                    + "#_st_global_busy_layer._st_busy_on._st_busy_pending{pointer-events:none;}"
                    + "#_st_global_busy_layer ._st_busy_back{position:absolute;inset:0;"
                    + "background:rgba(15,23,42,0.55);backdrop-filter:blur(2px);}"
                    + "#_st_global_busy_layer ._st_busy_card{position:relative;z-index:1;"
                    + "min-width:min(22rem,90vw);max-width:90vw;padding:1.75rem 2rem;border-radius:1rem;"
                    + "background:#f8fafc;box-shadow:0 25px 50px -12px rgba(0,0,0,0.35);"
                    + "text-align:center;font-family:system-ui,sans-serif;color:#0f172a;}"
                    + "#_st_global_busy_layer ._st_busy_ring{width:3rem;height:3rem;margin:0 auto 1rem;"
                    + "border:0.35rem solid #cbd5e1;border-top-color:#2563eb;border-radius:50%;"
                    + "animation:_st_busy_spin 0.75s linear infinite;}"
                    + "@keyframes _st_busy_spin{to{transform:rotate(360deg);}}"
                    + "#_st_global_busy_layer ._st_busy_title{font-size:1.15rem;font-weight:600;margin:0 0 0.35rem;}"
                    + "#_st_global_busy_layer ._st_busy_sub{font-size:0.85rem;margin:0;opacity:0.75;line-height:1.4;}";
                var st = doc.createElement("style");
                st.id = "_st_global_busy_layer_style";
                st.textContent = css;
                doc.head.appendChild(st);
            }

            function ensureLayer() {
                ensureStyle();
                if (doc.getElementById("_st_global_busy_layer")) return;
                var layer = doc.createElement("div");
                layer.id = "_st_global_busy_layer";
                layer.setAttribute("aria-live", "polite");
                layer.setAttribute("aria-busy", "true");
                layer.innerHTML = "<div class=\\"_st_busy_back\\"></div>"
                    + "<div class=\\"_st_busy_card\\"><div class=\\"_st_busy_ring\\"></div>"
                    + "<p class=\\"_st_busy_title\\">読み込み中です…</p>"
                    + "<p class=\\"_st_busy_sub\\">しばらくお待ちください（画面の操作は一時的に無効です）</p></div>";
                doc.body.appendChild(layer);
            }

            function layerEl() {
                return doc.getElementById("_st_global_busy_layer");
            }

            function applyForceBusyTitle() {
                var pageId = doc.body.getAttribute("data-st-active-page-id") || "";
                var nodes = doc.querySelectorAll("._st_force_busy_marker, #_st_force_busy_marker");
                var m = null;
                for (var i = 0; i < nodes.length; i++) {
                    if ((nodes[i].getAttribute("data-st-page") || "") === pageId) {
                        m = nodes[i];
                        break;
                    }
                }
                var L = layerEl();
                if (!m || !L) return;
                var t = m.getAttribute("data-busy-title");
                if (!t) return;
                var titleEl = L.querySelector("._st_busy_title");
                var subEl = L.querySelector("._st_busy_sub");
                if (titleEl) titleEl.textContent = t;
                if (subEl) subEl.textContent = "しばらくお待ちください（画面の操作は一時的に無効です）";
            }

            function setBusy(on) {
                var L = layerEl();
                if (!L) return;
                if (on) {
                    L.classList.add("_st_busy_on");
                    if (S.state === "pending") L.classList.add("_st_busy_pending");
                    else L.classList.remove("_st_busy_pending");
                    if (S.state === "force" || forceBusyActive()) applyForceBusyTitle();
                } else {
                    L.classList.remove("_st_busy_on", "_st_busy_pending");
                }
            }

            var S = doc._stGlobalBusyOverlay || (doc._stGlobalBusyOverlay = {});

            function clearHideSpin() {
                if (S.hideSpinTimer) {
                    clearTimeout(S.hideSpinTimer);
                    S.hideSpinTimer = null;
                }
            }

            function clearPending() {
                if (S.pendingTimer) {
                    clearTimeout(S.pendingTimer);
                    S.pendingTimer = null;
                }
            }

            function clearMoDeb() {
                if (S.moDebounce) {
                    clearTimeout(S.moDebounce);
                    S.moDebounce = null;
                }
            }

            function hasSpinner() {
                return !!(doc.querySelector("[data-testid=\\"stSpinner\\"]"));
            }

            function forceBusyActive() {
                var pageId = doc.body.getAttribute("data-st-active-page-id") || "";
                var nodes = doc.querySelectorAll("._st_force_busy_marker, #_st_force_busy_marker");
                for (var i = 0; i < nodes.length; i++) {
                    if ((nodes[i].getAttribute("data-st-page") || "") === pageId) {
                        return true;
                    }
                }
                return false;
            }

            function releaseBusyOverlay() {
                clearHideSpin();
                clearPending();
                S.state = "idle";
                setBusy(false);
            }

            function reconnectObserver() {
                if (!S.mo || !S.handlersInstalled) return;
                try {
                    if (S.observedBody && doc.body && S.observedBody === doc.body) return;
                    S.mo.disconnect();
                } catch (e1) {}
                try {
                    S.mo.observe(doc.body, { childList: true, subtree: true });
                    S.observedBody = doc.body;
                } catch (e2) {}
            }

            function syncFromDom() {
                ensureLayer();
                reconnectObserver();
                var L = layerEl();
                if (!L) return;

                if (hasSpinner() || forceBusyActive()) {
                    clearHideSpin();
                    clearPending();
                    S.state = hasSpinner() ? "spin" : "force";
                    setBusy(true);
                    return;
                }

                if (S.state === "spin" || S.state === "force") {
                    if (S.state === "force") {
                        if (forceBusyActive()) {
                            setBusy(true);
                            return;
                        }
                        S.state = "idle";
                        setBusy(false);
                        return;
                    }
                    if (S.hideSpinTimer) return;
                    S.hideSpinTimer = setTimeout(function() {
                        S.hideSpinTimer = null;
                        if (!hasSpinner() && S.state === "spin") {
                            S.state = "idle";
                            setBusy(false);
                        }
                    }, SPIN_HIDE_MS);
                    return;
                }
                releaseBusyOverlay();
            }

            function scheduleSync() {
                clearMoDeb();
                S.moDebounce = setTimeout(function() {
                    S.moDebounce = null;
                    syncFromDom();
                }, MO_DEBOUNCE_MS);
            }

            ensureLayer();

            function isRerunTriggerTarget(t) {
                if (!t || !t.closest) return false;
                if (!t.closest("[data-testid=\\"stApp\\"]")) return false;
                if (t.closest("#_st_global_busy_layer")) return false;
                if (t.closest("[data-testid=\\"stChatInput\\"]")) return false;
                if (t.closest("[data-testid=\\"stFileUploader\\"]")) return false;
                if (t.closest('a[href^="http"]') || t.closest('a[href^="https"]')) return false;
                if (t.closest("[data-testid=\\"stSidebar\\"]") && t.closest("button")) {
                    /* サイドバー遷移では pending オーバーレイを出さない（解除漏れで永久表示になる） */
                    if (S.state === "pending" || S.state === "force") {
                        clearPending();
                        S.state = "idle";
                        setBusy(false);
                    }
                    return false;
                }
                if (t.closest("[data-testid=\\"stFormSubmitButton\\"]")) return true;
                /* フォーム内は送信まで再実行しない（± 等で読込オーバーレイを出さない） */
                if (t.closest("[data-testid=\\"stForm\\"]")) return false;
                if (t.closest("[data-testid=\\"stSelectbox\\"]")) return false;
                if (t.closest("[data-testid=\\"stMultiSelect\\"]")) return false;
                if (t.closest("[data-baseweb=\\"popover\\"]")) return false;
                if (t.closest("[data-baseweb=\\"menu\\"]")) return false;
                if (t.closest("[data-testid=\\"stDialog\\"]")) return false;
                if (t.closest("[data-testid=\\"stDownloadButton\\"]")) return true;
                if (t.closest(".stButton") && t.tagName === "BUTTON") return true;
                if (t.closest("[data-testid=\\"stBaseButton\\"]") && t.tagName === "BUTTON") return true;
                if (t.closest("[data-testid=\\"stCheckbox\\"]")) return true;
                if (t.closest("[data-testid=\\"stRadio\\"]")) return true;
                if (t.closest("[data-testid=\\"stNumberInput\\"]") && t.tagName === "BUTTON") return true;
                return false;
            }

            if (!S.handlersInstalled) {
                S.handlersInstalled = true;
                clearHideSpin();
                clearPending();
                clearMoDeb();
                setBusy(false);
                S.state = "idle";
                S.mo = new MutationObserver(scheduleSync);
                try {
                    S.mo.observe(doc.body, { childList: true, subtree: true });
                    S.observedBody = doc.body;
                } catch (e3) {}

                doc.addEventListener("pointerdown", function(e) {
                    if (!isRerunTriggerTarget(e.target)) return;
                    if (hasSpinner() || forceBusyActive()) return;
                    if (S.state === "spin" || S.state === "force") return;
                    S.state = "pending";
                    setBusy(true);
                    clearPending();
                    S.pendingTimer = setTimeout(function() {
                        S.pendingTimer = null;
                        if (S.state === "pending" && !hasSpinner() && !forceBusyActive()) {
                            S.state = "idle";
                            setBusy(false);
                        }
                    }, PENDING_MAX_MS);
                }, true);

                setInterval(function() { syncFromDom(); }, 350);
            }

            syncFromDom();
        })();
        </script>
        """,
        height=0,
    )


def _clear_candidate_search_busy_if_left_page() -> None:
    """候補検索以外へ遷移したあと、セッションの強制ローディングが残らないようにする."""
    if st.session_state.get("_active_page_id") != "candidate_search":
        st.session_state.pop("candidate_search_ui_busy", None)
        st.session_state.pop("candidate_search_job", None)
        st.session_state.pop("candidate_search_calendar_pending", None)

def inject_wide_layout(*, skip_busy_reset: bool = False) -> None:
    """全ページで幅を統一するCSS・各種JSパッチを注入.

    app・各ページで呼び出し、レイアウト幅の差を解消する。
    全画面の読み込みレイヤー（stSpinner 連動・再実行トリガー）を注入する。
    メニューから遷移した直後は _sidebar_collapse により折りたたみボタンを JS でクリックする.
    skip_busy_reset: 候補検索の分割検索・カレンダー描画中は True（毎 rerun の解除を防ぐ）.
    """
    _clear_candidate_search_busy_if_left_page()
    page_id = str(st.session_state.get("_active_page_id") or "app")
    if not skip_busy_reset:
        _inject_page_busy_reset(page_id)
    should_collapse = st.session_state.pop("_sidebar_collapse", False)

    base_css = """
        [data-testid="stAppViewContainer"] > section { max-width: 100%; }
        .main .block-container { max-width: 100%; padding: 1rem 2rem; }
        #MainMenu {visibility: hidden;}
        iframe[height="0"] { display: none; }
    """

    st.markdown(f"<style>{base_css}</style>", unsafe_allow_html=True)
    _inject_global_busy_overlay()
    _inject_select_toggle_fix()
    if should_collapse:
        _inject_sidebar_collapse_js()
    inject_floating_inquiry_button()
