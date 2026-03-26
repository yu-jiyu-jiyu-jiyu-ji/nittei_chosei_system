from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

import streamlit as st


def _get_log_store() -> List[Dict[str, Any]]:
    """ログのダミーストアを取得.

    Firestore 未接続のため、Phase1 ではセッション内リストを疑似ストアとして利用する。
    """
    if "dummy_logs" not in st.session_state:
        # 初期ダミーログ
        st.session_state["dummy_logs"] = [
            {
                "log_id": "LOG_0001",
                "action": "confirm_schedule",
                "project_id": "PJT_0001",
                "schedule_id": "SCH_0001",
                "user_name": "事務担当A",
                "detail": "仮登録から確定へ変更",
                "created_at": datetime.utcnow().isoformat(),
            },
            {
                "log_id": "LOG_0002",
                "action": "create_project",
                "project_id": "PJT_0002",
                "schedule_id": None,
                "user_name": "開発ユーザー",
                "detail": "新規案件を登録",
                "created_at": datetime.utcnow().isoformat(),
            },
        ]
    return st.session_state["dummy_logs"]


def list_logs(filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """ログ一覧を取得（簡易フィルタ付き）."""
    logs = list(_get_log_store())
    filters = filters or {}

    action = (filters.get("action") or "").strip()
    user_name = (filters.get("user_name") or "").strip()
    date_from = filters.get("date_from")
    date_to = filters.get("date_to")

    def matches(log: Dict[str, Any]) -> bool:
        if action and action not in str(log.get("action", "")):
            return False
        if user_name and user_name not in str(log.get("user_name", "")):
            return False
        created = log.get("created_at", "")
        if date_from and created < str(date_from):
            return False
        if date_to and created > str(date_to):
            return False
        return True

    filtered = [l for l in logs if matches(l)]
    filtered.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return filtered
