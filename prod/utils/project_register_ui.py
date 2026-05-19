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
from utils.validation_util import validate_project_input

_WORK_DURATION_OPTIONS: List[int] = list(range(60, MAX_WORK_DURATION_MINUTES + 1, 30))
_WORKER_COUNT_OPTIONS: List[int] = list(range(1, MAX_REQUIRED_WORKERS + 1))
_VEHICLE_COUNT_OPTIONS: List[int] = list(range(0, MAX_REQUIRED_VEHICLES + 1))

_FORM_FIELD_KEYS = (
    "construction_type",
    "construction_type_other",
    "project_name",
    "customer_name",
    "address",
    "work_duration_minutes",
    "required_workers",
    "required_vehicle_count",
    "note",
)


def _field_key(key_prefix: str, suffix: str) -> str:
    return f"{key_prefix}_{suffix}"


def _clear_register_form_fields(key_prefix: str) -> None:
    for suffix in _FORM_FIELD_KEYS:
        st.session_state.pop(_field_key(key_prefix, suffix), None)


def _collect_register_form_values(key_prefix: str) -> Dict[str, object]:
    return {
        "project_name": st.session_state.get(_field_key(key_prefix, "project_name"), ""),
        "customer_name": st.session_state.get(_field_key(key_prefix, "customer_name"), ""),
        "address": st.session_state.get(_field_key(key_prefix, "address"), ""),
        "construction_type": st.session_state.get(_field_key(key_prefix, "construction_type"), []),
        "construction_type_other": st.session_state.get(
            _field_key(key_prefix, "construction_type_other"), ""
        ),
        "work_duration_minutes": st.session_state.get(
            _field_key(key_prefix, "work_duration_minutes"), _WORK_DURATION_OPTIONS[0]
        ),
        "required_workers": st.session_state.get(
            _field_key(key_prefix, "required_workers"), _WORKER_COUNT_OPTIONS[0]
        ),
        "required_vehicle_count": st.session_state.get(
            _field_key(key_prefix, "required_vehicle_count"), 0
        ),
        "note": st.session_state.get(_field_key(key_prefix, "note"), ""),
    }


def _handle_register_submit(key_prefix: str, expanded_key: str) -> None:
    """フォーム送信コールバック（session_state から値を読み登録）."""
    form_values = _collect_register_form_values(key_prefix)
    is_valid, errors = validate_project_input(form_values)
    if not is_valid:
        st.session_state[_field_key(key_prefix, "register_errors")] = errors
        return

    st.session_state.pop(_field_key(key_prefix, "register_errors"), None)
    try:
        project = create_project(
            form_values,
            current_user_name=st.session_state.get("current_user_name"),
        )
    except FirestoreSaveError as e:
        st.session_state[_field_key(key_prefix, "register_errors")] = [f"保存に失敗しました。{e}"]
        return
    except FirestoreConnectionError:
        st.session_state[_field_key(key_prefix, "register_errors")] = [DB_UNAVAILABLE_MESSAGE]
        return
    except Exception as exc:
        st.session_state[_field_key(key_prefix, "register_errors")] = [
            f"想定外エラーが発生しました: {exc}"
        ]
        return

    st.session_state[_field_key(key_prefix, "register_created")] = project
    st.session_state[expanded_key] = False
    _clear_register_form_fields(key_prefix)


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

    register_errors: Optional[List[str]] = st.session_state.get(_field_key(key_prefix, "register_errors"))

    with st.expander(title, expanded=bool(st.session_state.get(expanded_key, False))):
        if register_errors:
            st.error("必須未入力または不正な値があります。")
            for msg in register_errors:
                st.write(f"- {msg}")

        with st.form(key=_field_key(key_prefix, "project_register_form"), clear_on_submit=False):
            st.multiselect(
                "施工内容*（複数選択可）",
                options=CONSTRUCTION_TYPE_OPTIONS,
                key=_field_key(key_prefix, "construction_type"),
            )
            st.text_input(
                "施工内容詳細（「その他」選択時は必須）",
                key=_field_key(key_prefix, "construction_type_other"),
                placeholder="自由入力",
            )

            col_left, col_right = st.columns(2)
            with col_left:
                st.text_input("案件名*", key=_field_key(key_prefix, "project_name"))
                st.text_input("顧客名*", key=_field_key(key_prefix, "customer_name"))
                st.text_input("住所*", key=_field_key(key_prefix, "address"))

            with col_right:
                st.selectbox(
                    "作業時間（分）*",
                    options=_WORK_DURATION_OPTIONS,
                    key=_field_key(key_prefix, "work_duration_minutes"),
                )
                st.selectbox(
                    "必要人数*",
                    options=_WORKER_COUNT_OPTIONS,
                    key=_field_key(key_prefix, "required_workers"),
                )
                st.selectbox(
                    "必要車両数（任意）",
                    options=_VEHICLE_COUNT_OPTIONS,
                    key=_field_key(key_prefix, "required_vehicle_count"),
                )

            st.text_area("備考", key=_field_key(key_prefix, "note"))

            st.form_submit_button(
                "新規登録",
                on_click=_handle_register_submit,
                kwargs={"key_prefix": key_prefix, "expanded_key": expanded_key},
            )

    return st.session_state.pop(_field_key(key_prefix, "register_created"), None)
