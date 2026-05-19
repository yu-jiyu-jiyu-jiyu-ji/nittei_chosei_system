"""案件新規登録フォーム（案件一覧・候補検索で共通利用）."""

from __future__ import annotations

from typing import Any, Dict, Optional

import streamlit as st

from config.constants import CONSTRUCTION_TYPE_OPTIONS, DB_UNAVAILABLE_MESSAGE
from services.firestore_service import FirestoreConnectionError, FirestoreSaveError
from services.project_service import create_project
from utils.loading_util import visible_spinner
from utils.validation_util import validate_project_input


def render_project_register_expander(
    *,
    key_prefix: str = "proj_reg",
    expanded: bool = False,
    title: str = "案件を新規登録",
) -> Optional[Dict[str, Any]]:
    """新規登録エキスパンダーを描画する。成功時は作成した案件 dict を返す."""
    created: Optional[Dict[str, Any]] = None
    with st.expander(title, expanded=expanded):
        with st.form(key=f"{key_prefix}_project_register_form", clear_on_submit=False):
            new_construction_type = st.multiselect(
                "施工内容*（複数選択可）",
                options=CONSTRUCTION_TYPE_OPTIONS,
                key=f"{key_prefix}_construction_type",
            )
            new_construction_type_other = st.text_input(
                "施工内容詳細（「その他」選択時は必須）",
                key=f"{key_prefix}_construction_type_other",
                placeholder="自由入力",
            )

            col_left, col_right = st.columns(2)
            with col_left:
                new_project_name = st.text_input("案件名*", key=f"{key_prefix}_project_name")
                new_customer_name = st.text_input("顧客名*", key=f"{key_prefix}_customer_name")
                new_address = st.text_input("住所*", key=f"{key_prefix}_address")

            with col_right:
                new_work_duration = st.number_input(
                    "作業時間（分）*",
                    min_value=0,
                    step=30,
                    value=60,
                    key=f"{key_prefix}_work_duration_minutes",
                )
                new_required_workers = st.number_input(
                    "必要人数*",
                    min_value=0,
                    step=1,
                    value=1,
                    key=f"{key_prefix}_required_workers",
                )
                new_required_vehicle_count = st.number_input(
                    "必要車両数（任意）",
                    min_value=0,
                    step=1,
                    value=0,
                    key=f"{key_prefix}_required_vehicle_count",
                )

            new_note = st.text_area("備考", key=f"{key_prefix}_note")

            submitted_reg = st.form_submit_button("新規登録")
            if submitted_reg:
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
                    except FirestoreSaveError as e:
                        st.error(f"保存に失敗しました。{e}")
                    except FirestoreConnectionError:
                        st.error(DB_UNAVAILABLE_MESSAGE)
                    except Exception as exc:
                        st.error("想定外エラーが発生しました。")
                        st.exception(exc)
    return created
