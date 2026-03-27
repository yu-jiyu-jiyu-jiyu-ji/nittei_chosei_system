"""旧「ログ一覧」URL互換用。運用ログはファイルに記録する."""

from __future__ import annotations

import streamlit as st

from config.constants import APP_TITLE
from utils.layout_util import STREAMLIT_MENU_ITEMS, inject_sidebar_nav, inject_wide_layout
from utils.session_util import init_session_state


def render_page() -> None:
    st.set_page_config(
        page_title=f"{APP_TITLE} - ログ",
        layout="wide",
        menu_items=STREAMLIT_MENU_ITEMS,
    )
    init_session_state()
    inject_wide_layout()
    inject_sidebar_nav()

    st.title("ログ")
    st.info(
        "操作ログはサーバー上の **logs/app.log**（テキスト）に追記されます。"
        "画面からは参照しません。必要に応じてエンジニアがファイルを直接確認してください。"
    )


if __name__ == "__main__":
    render_page()
