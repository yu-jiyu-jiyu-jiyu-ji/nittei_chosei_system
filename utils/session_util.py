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
    "google_calendar_tokens": {},  # worker_id / vehicle_id / vehicle_fleet -> {"refresh_token": str}（セッション）
    "candidate_location_overrides": {},  # "worker_id:event_id" -> 暫定住所
}


def init_session_state() -> None:
    """セッション状態の初期化."""
    for key, default_value in SESSION_DEFAULTS.items():
        if key not in st.session_state:
            st.session_state[key] = default_value
