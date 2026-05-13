from __future__ import annotations

from typing import Any, Dict

import streamlit as st

from config.status_labels import STATUS_LABELS
from services.project_service import list_projects
from utils.display_util import projects_to_dataframe
from utils.session_util import init_session_state


def render_page() -> None:
    """案件一覧画面."""
    st.title("案件一覧")
    st.caption("登録済み案件を一覧表示します。検索・詳細表示・候補検索へ遷移できます。")

    init_session_state()

    with st.form("project_search_form"):
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            project_name = st.text_input("案件名（部分一致）", key="search_project_name")
        with col2:
            customer_name = st.text_input("顧客名（部分一致）", key="search_customer_name")
        with col3:
            status_label = st.selectbox(
                "ステータス",
                options=[""] + list(STATUS_LABELS.keys()),
                format_func=lambda v: STATUS_LABELS.get(v, "") if v else "",
                key="search_status",
            )
        with col4:
            st.write("")
            st.write("")

        col_btn1, col_btn2, col_btn3, col_btn4 = st.columns(4)
        with col_btn1:
            do_search = st.form_submit_button("検索")
        with col_btn2:
            clear = st.form_submit_button("条件クリア")
        with col_btn3:
            go_new = st.form_submit_button("新規案件登録へ")

    if clear:
        for k in ("search_project_name", "search_customer_name", "search_status"):
            if k in st.session_state:
                del st.session_state[k]
        st.rerun()

    if go_new:
        st.switch_page("pages/01_案件登録.py")

    filters = {}
    if do_search or "search_project_name" in st.session_state:
        filters = {
            "project_name": project_name,
            "customer_name": customer_name,
            "status": status_label,
        }

    try:
        projects = list_projects(filters)
    except Exception as exc:
        st.error("案件一覧の取得に失敗しました（想定外エラー）。")
        st.exception(exc)
        return

    if not projects:
        st.warning("該当する案件がありません。")
        return

    df = projects_to_dataframe(projects)
    st.dataframe(df, use_container_width=True)

    # 案件選択（詳細・候補検索用）
    st.markdown("---")
    st.markdown("### 操作")
    project_options = {
        f"{p.get('project_id', '')} - {p.get('project_name', '')}": p for p in projects
    }
    selected_label = st.selectbox(
        "対象案件を選択",
        options=[""] + list(project_options.keys()),
        format_func=lambda v: v if v else "（選択してください）",
        key="project_list_select",
    )
    selected_project_for_action = project_options.get(selected_label) if selected_label else None

    col_detail, col_search, col_edit = st.columns(3)
    with col_detail:
        if st.button("詳細表示", key="btn_detail"):
            if selected_project_for_action:
                st.session_state["selected_project"] = selected_project_for_action
                st.session_state["selected_project_id"] = selected_project_for_action.get("project_id")
                st.switch_page("pages/02_案件詳細.py")
            else:
                st.warning("対象案件を選択してください。")
    with col_search:
        if st.button("候補検索へ", key="btn_candidate_search"):
            if selected_project_for_action:
                st.session_state["selected_project"] = selected_project_for_action
                st.session_state["selected_project_id"] = selected_project_for_action.get("project_id")
                st.switch_page("pages/03_候補検索.py")
            else:
                st.warning("対象案件を選択してください。")
    with col_edit:
        if st.button("編集", key="btn_edit"):
            st.info("編集画面は 01_案件登録 を参照して別途実装予定です。")


if __name__ == "__main__":
    render_page()
