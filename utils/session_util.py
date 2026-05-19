from __future__ import annotations

from typing import Any, Dict

import streamlit as st


SESSION_DEFAULTS: Dict[str, Any] = {
    "selected_project_id": None,
    "selected_project": None,
    # candidate_results は検索実行後のみ設定（初期化すると「未検索」と判別できない）
    "selected_candidate": None,
    "search_filters": {},
    "current_user_role": "admin",
    "current_user_name": "開発ユーザー",
    "current_user_email": "dev@example.local",
    "google_calendar_tokens": {},  # worker_id / vehicle_id / vehicle_fleet -> {"refresh_token": str}（セッション）
    "candidate_location_overrides": {},  # "worker_id:event_id" -> 暫定住所
}


def init_session_state() -> None:
    """セッション状態の初期化."""
    for key, default_value in SESSION_DEFAULTS.items():
        if key not in st.session_state:
            st.session_state[key] = default_value


def _clear_candidate_search_state_for_new_project() -> None:
    for key in (
        "candidate_results",
        "candidate_search_job",
        "candidate_search_calendar_pending",
        "_candidate_search_masters",
        "candidate_cal_chunk",
        "_candidate_cal_chunk_week",
        "candidate_dialog_id",
    ):
        st.session_state.pop(key, None)
    st.session_state.pop("week_nav_trigger_search", None)


def apply_registered_project_to_candidate_search(project: Dict[str, Any]) -> bool:
    """登録した案件を候補検索の条件に反映し、検索を開始する."""
    pname = str(project.get("project_name") or "").strip()
    if not pname:
        return False
    st.session_state["selected_project"] = project
    st.session_state["selected_project_id"] = project.get("project_id")
    st.session_state["candidate_search_project_select"] = pname
    st.session_state["_candidate_sync_project_key"] = pname
    try:
        rw = int(project.get("required_workers") or 0)
        st.session_state["candidate_search_capacity"] = max(0, rw)
    except (TypeError, ValueError):
        pass
    _clear_candidate_search_state_for_new_project()
    st.session_state["_candidate_search_btn_pressed"] = True
    st.session_state["candidate_search_post_register_notice"] = (
        f"案件「{pname}」を登録しました。候補を検索しています。"
    )
    return True


def navigate_to_candidate_search_after_register(project: Dict[str, Any]) -> None:
    """新規案件登録後、候補検索へ案件を引き継ぎ検索を開始する."""
    if not apply_registered_project_to_candidate_search(project):
        return
    st.session_state["_sidebar_collapse"] = True
    st.switch_page("pages/03_候補検索.py")
