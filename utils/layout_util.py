"""レイアウト統一ユーティリティ."""

from __future__ import annotations

import streamlit as st


def inject_sidebar_nav() -> None:
    """サイドバーにページナビを注入（config.toml の showSidebarNavigation=false 用）."""
    st.sidebar.markdown("### メニュー")
    st.sidebar.page_link("pages/01_案件一覧.py", label="案件一覧", icon="📋")
    st.sidebar.page_link("pages/03_候補検索.py", label="候補検索", icon="🔍")
    st.sidebar.page_link("pages/04_共通設定.py", label="共通設定", icon="⚙️")
    st.sidebar.page_link("pages/05_ログ一覧.py", label="ログ一覧", icon="📜")


def inject_wide_layout() -> None:
    """全ページで幅を統一するCSSを注入.

    app・各ページで呼び出し、レイアウト幅の差を解消する。
    """
    st.markdown(
        """
        <style>
        [data-testid="stAppViewContainer"] > section { max-width: 100%; }
        .main .block-container { max-width: 100%; padding: 1rem 2rem; }
        </style>
        """,
        unsafe_allow_html=True,
    )
