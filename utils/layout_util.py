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


def inject_sidebar_nav() -> None:
    """サイドバーにページナビを注入（config.toml の showSidebarNavigation=false 用）."""
    st.sidebar.markdown("### メニュー")
    st.sidebar.page_link("pages/01_案件一覧.py", label="案件一覧", icon="📋")
    st.sidebar.page_link("pages/03_候補検索.py", label="候補検索", icon="🔍")
    st.sidebar.page_link("pages/04_共通設定.py", label="共通設定", icon="⚙️")
    st.sidebar.page_link("pages/06_問い合わせ履歴.py", label="問い合わせ履歴", icon="💬")
    if st.session_state.get("current_user_role") == "admin":
        st.sidebar.page_link("pages/07_問い合わせ管理.py", label="問い合わせ管理", icon="📨")


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


def _inject_sidebar_auto_collapse() -> None:
    """サイドバーのナビリンク押下後にサイドバーを自動で畳む JS パッチ.

    page_link によるページ遷移では Streamlit が画面を再描画するため、
    遷移前に即座に折りたたんでも復元されてしまう。localStorage に
    フラグを保存し、遷移先ページのロード時にリトライ付きで折りたたむ。
    """
    components.html(
        """
        <script>
        (function() {
            var doc = window.parent.document;
            var storage = window.parent.localStorage;
            var KEY = '_st_sidebar_collapse';

            function collapseSidebar() {
                var sidebar = doc.querySelector('[data-testid="stSidebar"]');
                var btn =
                    doc.querySelector('[data-testid="stSidebarCollapseButton"] button') ||
                    doc.querySelector('[data-testid="stSidebarCollapseButton"]') ||
                    (sidebar && sidebar.querySelector('[data-testid="stBaseButton-headerNoPadding"]')) ||
                    (sidebar && sidebar.querySelector('header button'));
                if (btn) { btn.click(); return true; }
                return false;
            }

            if (storage.getItem(KEY)) {
                storage.removeItem(KEY);
                var attempts = 0;
                (function tryCollapse() {
                    if (collapseSidebar() || ++attempts > 15) return;
                    setTimeout(tryCollapse, 80);
                })();
            }

            if (doc._sidebarAutoCollapseApplied) return;
            doc._sidebarAutoCollapseApplied = true;

            doc.addEventListener('click', function(e) {
                var sidebar = e.target.closest('[data-testid="stSidebar"]');
                if (!sidebar) return;
                var link = e.target.closest('a');
                if (!link || link.target === '_blank') return;

                storage.setItem(KEY, '1');
                requestAnimationFrame(function() { collapseSidebar(); });
            });
        })();
        </script>
        """,
        height=0,
    )


def inject_wide_layout() -> None:
    """全ページで幅を統一するCSS・各種JSパッチを注入.

    app・各ページで呼び出し、レイアウト幅の差を解消する。
    """
    st.markdown(
        """
        <style>
        [data-testid="stAppViewContainer"] > section { max-width: 100%; }
        .main .block-container { max-width: 100%; padding: 1rem 2rem; }
        #MainMenu {visibility: hidden;}
        iframe[height="0"] { display: none; }
        </style>
        """,
        unsafe_allow_html=True,
    )
    _inject_select_toggle_fix()
    _inject_sidebar_auto_collapse()
    inject_floating_inquiry_button()
