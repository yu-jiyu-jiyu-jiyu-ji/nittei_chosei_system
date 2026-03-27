"""エントリポイント。起動時は候補検索ページへ遷移する."""

import streamlit as st

from config.constants import APP_TITLE
from utils.layout_util import STREAMLIT_MENU_ITEMS
from utils.session_util import init_session_state


def main() -> None:
    st.set_page_config(page_title=APP_TITLE, layout="wide", menu_items=STREAMLIT_MENU_ITEMS)
    init_session_state()
    st.switch_page("pages/03_候補検索.py")


if __name__ == "__main__":
    main()
