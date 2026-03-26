from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd
import streamlit as st

from config.constants import APP_TITLE
from services.log_service import list_logs
from utils.layout_util import inject_sidebar_nav, inject_wide_layout
from utils.session_util import init_session_state


def logs_to_dataframe(logs: List[Dict[str, Any]]) -> pd.DataFrame:
    """ログ一覧表示用のDataFrameを生成."""
    if not logs:
        return pd.DataFrame(
            columns=[
                "ログID",
                "操作内容",
                "案件ID",
                "予定ID",
                "操作者",
                "詳細",
                "作成日時",
            ]
        )

    rows = []
    for log in logs:
        rows.append(
            {
                "ログID": log.get("log_id"),
                "操作内容": log.get("action"),
                "案件ID": log.get("project_id") or "-",
                "予定ID": log.get("schedule_id") or "-",
                "操作者": log.get("user_name") or "-",
                "詳細": log.get("detail") or "-",
                "作成日時": log.get("created_at"),
            }
        )
    return pd.DataFrame(rows)


def render_page() -> None:
    """ログ一覧画面."""
    st.set_page_config(page_title=f"{APP_TITLE} - ログ一覧", layout="wide")
    init_session_state()
    inject_wide_layout()
    inject_sidebar_nav()

    st.title("ログ一覧")
    st.caption("操作履歴を確認します。検索条件で絞り込みができます。")

    with st.form("log_search_form"):
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            action_filter = st.text_input("操作内容（部分一致）", key="search_action")
        with col2:
            user_filter = st.text_input("操作者（部分一致）", key="search_user_name")
        with col3:
            st.write("")
            st.write("")
        with col4:
            st.write("")
            st.write("")

        col_btn1, col_btn2, col_btn3 = st.columns(3)
        with col_btn1:
            do_search = st.form_submit_button("検索")
        with col_btn2:
            clear = st.form_submit_button("条件クリア")
        with col_btn3:
            refresh = st.form_submit_button("一覧更新")

    if clear:
        for k in ("search_action", "search_user_name"):
            if k in st.session_state:
                del st.session_state[k]
        st.rerun()

    filters = {}
    if do_search or refresh or "search_action" in st.session_state:
        filters = {
            "action": action_filter,
            "user_name": user_filter,
        }

    try:
        logs = list_logs(filters)
    except Exception as exc:
        st.error("ログ一覧の取得に失敗しました。")
        st.exception(exc)
        return

    if not logs:
        st.warning("該当するログがありません。")
        return

    df = logs_to_dataframe(logs)
    st.dataframe(df, use_container_width=True)


if __name__ == "__main__":
    render_page()
