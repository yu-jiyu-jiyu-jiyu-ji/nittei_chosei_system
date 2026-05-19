"""案件新規登録フォーム（案件一覧・候補検索で共通利用）."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import streamlit as st

from config.constants import (
    CONSTRUCTION_TYPE_OPTIONS,
    DB_UNAVAILABLE_MESSAGE,
    MAX_REQUIRED_VEHICLES,
    MAX_REQUIRED_WORKERS,
    MAX_WORK_DURATION_MINUTES,
)
from services.firestore_service import FirestoreConnectionError, FirestoreSaveError
from services.project_service import create_project
from utils.loading_util import visible_spinner
from utils.validation_util import validate_project_input

_WORK_DURATION_OPTIONS: List[int] = list(range(60, MAX_WORK_DURATION_MINUTES + 1, 30))
_WORKER_COUNT_OPTIONS: List[int] = list(range(1, MAX_REQUIRED_WORKERS + 1))
_VEHICLE_COUNT_OPTIONS: List[int] = list(range(0, MAX_REQUIRED_VEHICLES + 1))


def _field_key(key_prefix: str, suffix: str) -> str:
    return f"{key_prefix}_{suffix}"


def render_project_register_expander(
    *,
    key_prefix: str = "proj_reg",
    expanded: bool = False,
    expanded_session_key: Optional[str] = None,
    title: str = "案件を新規登録",
) -> Optional[Dict[str, Any]]:
    """新規登録エキスパンダーを描画する。成功時は作成した案件 dict を返す."""
    expanded_key = expanded_session_key or _field_key(key_prefix, "register_expanded")
    if expanded_key not in st.session_state:
        st.session_state[expanded_key] = expanded

    with st.expander(title, expanded=bool(st.session_state.get(expanded_key, False))):
        with st.form(key=_field_key(key_prefix, "project_register_form"), clear_on_submit=False):
            new_construction_type = st.multiselect(
                "施工内容*（複数選択可）",
                options=CONSTRUCTION_TYPE_OPTIONS,
                key=_field_key(key_prefix, "construction_type"),
            )
            new_construction_type_other = st.text_input(
                "施工内容詳細（「その他」選択時は必須）",
                key=_field_key(key_prefix, "construction_type_other"),
                placeholder="自由入力",
            )

            col_left, col_right = st.columns(2)
            with col_left:
                new_project_name = st.text_input("案件名*", key=_field_key(key_prefix, "project_name"))
                new_customer_name = st.text_input("顧客名*", key=_field_key(key_prefix, "customer_name"))
                new_address = st.text_input("住所*", key=_field_key(key_prefix, "address"))

            with col_right:
                new_work_duration = st.selectbox(
                    "作業時間（分）*",
                    options=_WORK_DURATION_OPTIONS,
                    key=_field_key(key_prefix, "work_duration_minutes"),
                )
                new_required_workers = st.selectbox(
                    "必要人数*",
                    options=_WORKER_COUNT_OPTIONS,
                    key=_field_key(key_prefix, "required_workers"),
                )
                new_required_vehicle_count = st.selectbox(
                    "必要車両数（任意）",
                    options=_VEHICLE_COUNT_OPTIONS,
                    key=_field_key(key_prefix, "required_vehicle_count"),
                )

            new_note = st.text_area("備考", key=_field_key(key_prefix, "note"))

            submitted = st.form_submit_button("新規登録")
            if submitted:
                form_values = {
                    "project_name": new_project_name,
                    "customer_name": new_customer_name,
                    "address": new_address,
                    "construction_type": new_construction_type,
                    "construction_type_other": new_construction_type_other,
                    "work_duration_minutes": new_work_duration,
                    "required_workers": new_required_workers,
                    "required_vehicle_count": new_required_vehicle_count,
                    "note": new_note,
                }
                is_valid, errors = validate_project_input(form_values)
                if not is_valid:
                    st.error("必須未入力または不正な値があります。")
                    for msg in errors:
                        st.write(f"- {msg}")
                else:
                    try:
                        with visible_spinner("登録中…"):
                            created = create_project(
                                form_values,
                                current_user_name=st.session_state.get("current_user_name"),
                            )
                        st.session_state[_field_key(key_prefix, "register_created")] = created
                        st.session_state[expanded_key] = False
                    except FirestoreSaveError as e:
                        st.error(f"保存に失敗しました。{e}")
                    except FirestoreConnectionError:
                        st.error(DB_UNAVAILABLE_MESSAGE)
                    except Exception as exc:
                        st.error("想定外エラーが発生しました。")
                        st.exception(exc)

    return st.session_state.pop(_field_key(key_prefix, "register_created"), None)
