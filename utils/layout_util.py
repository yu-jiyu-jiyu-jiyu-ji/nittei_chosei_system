"""レイアウト統一ユーティリティ."""

from __future__ import annotations

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
    """プルダウンをクリックで開閉トグルできるようにする JS パッチ."""
    components.html(
        """
        <script>
        (function() {
            var doc = window.parent.document;
            if (doc._selectToggleFixApplied) return;
            doc._selectToggleFixApplied = true;

            doc.addEventListener('mousedown', function(e) {
                var select = e.target.closest('[data-baseweb="select"]');
                if (!select) return;
                if (e.target.closest('[role="listbox"]')
                    || e.target.closest('[role="option"]')
                    || e.target.closest('[data-baseweb="popover"]')
                    || e.target.closest('[data-baseweb="tag"]')) return;

                var input = select.querySelector('input');
                if (input && doc.activeElement === input) {
                    e.preventDefault();
                    input.blur();
                }
            });
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
                if (btn) { btn.click(); return true; }
                var sb = doc.querySelector('[data-testid="stSidebar"]');
                if (sb && sb.getAttribute('aria-expanded') === 'true') {
                    sb.setAttribute('aria-expanded', 'false');
                    return true;
                }
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


_SIDEBAR_FORCE_COLLAPSE_CSS = """
[data-testid="stSidebar"][aria-expanded="true"] {
    transform: translateX(-100%) !important;
    transition: none !important;
    visibility: hidden !important;
}
"""


def inject_wide_layout() -> None:
    """全ページで幅を統一するCSS・各種JSパッチを注入.

    app・各ページで呼び出し、レイアウト幅の差を解消する。
    メニューから遷移した直後は _sidebar_collapse によりサイドバーを閉じる。
    """
    should_collapse = st.session_state.pop("_sidebar_collapse", False)

    base_css = """
        [data-testid="stAppViewContainer"] > section { max-width: 100%; }
        .main .block-container { max-width: 100%; padding: 1rem 2rem; }
        #MainMenu {visibility: hidden;}
        iframe[height="0"] { display: none; }
    """
    if should_collapse:
        base_css += _SIDEBAR_FORCE_COLLAPSE_CSS

    st.markdown(f"<style>{base_css}</style>", unsafe_allow_html=True)
    _inject_select_toggle_fix()
    if should_collapse:
        _inject_sidebar_collapse_js()
    inject_floating_inquiry_button()
