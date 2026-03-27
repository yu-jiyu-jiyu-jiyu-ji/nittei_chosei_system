from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

import streamlit as st

from config.constants import APP_TITLE, CONSTRUCTION_TYPE_OPTIONS, CONSTRUCTION_TYPE_OTHER, DB_UNAVAILABLE_MESSAGE
from config.status_labels import STATUS_LABELS
from services.firestore_service import FirestoreConnectionError, FirestoreSaveError
from services.project_service import create_project, delete_project, list_projects, update_project
from services.schedule_commit_service import remove_project_schedule_from_google
from services.setting_service import get_settings
from services.vehicle_service import list_vehicles
from services.worker_service import list_workers
from utils.display_util import format_status
from utils.layout_util import STREAMLIT_MENU_ITEMS, inject_sidebar_nav, inject_wide_layout
from utils.session_util import init_session_state
from utils.validation_util import validate_project_input


def _format_scheduled_at(raw: Optional[str]) -> str:
    """Firestore に保存した ISO 文字列を一覧向けに短く表示."""
    if not raw:
        return "—"
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return str(raw)


def _clear_detail_on_filter_change() -> None:
    st.session_state.pop("project_detail_id", None)


def _render_delete_dialog(project_id: str, project_name: str) -> None:
    @st.dialog("案件の削除")
    def _dlg() -> None:
        st.markdown(f"**{project_name}** を削除してもいいですか？")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("はい", type="primary", key="delete_confirm_yes"):
                try:
                    ok = delete_project(project_id, current_user_name=st.session_state.get("current_user_name"))
                    if ok:
                        st.session_state.pop("pending_delete_project_id", None)
                        st.session_state.pop("project_detail_id", None)
                        st.session_state["project_deleted_flash"] = True
                        st.rerun()
                    else:
                        st.error("削除対象の案件が見つかりません。")
                except FirestoreSaveError as e:
                    st.error(f"削除に失敗しました。{e}")
                except FirestoreConnectionError:
                    st.error(DB_UNAVAILABLE_MESSAGE)
        with c2:
            if st.button("いいえ", key="delete_confirm_no"):
                st.session_state.pop("pending_delete_project_id", None)
                st.rerun()

    _dlg()


def _render_project_detail_dialog(project: Dict[str, Any]) -> None:
    """案件詳細ポップアップ（表示→編集ボタンで編集モードへ）."""

    edit_key = f"project_detail_edit_{project.get('project_id')}"
    is_edit_mode = st.session_state.get(edit_key, False)

    @st.dialog("案件詳細")
    def _show_project_detail() -> None:
        nonlocal is_edit_mode

        if not is_edit_mode:
            # 表示モード
            st.write(f"**案件ID**：{project.get('project_id', '-')}")
            st.write(f"**案件名**：{project.get('project_name', '-')}")
            st.write(f"**顧客名**：{project.get('customer_name', '-')}")
            st.write(f"**現場住所**：{project.get('address', '-')}")
            ct_raw = project.get("construction_type")
            ct_list = ct_raw if isinstance(ct_raw, list) else ([ct_raw] if ct_raw else [])
            ct_other = project.get("construction_type_other")
            ct_display = "、".join(ct_list) if ct_list else "-"
            if CONSTRUCTION_TYPE_OTHER in ct_list and ct_other:
                ct_display = ct_display.replace(CONSTRUCTION_TYPE_OTHER, f"{CONSTRUCTION_TYPE_OTHER}（{ct_other}）", 1)
            st.write(f"**施工内容**：{ct_display}")
            st.write(
                f"**ステータス**：{format_status(str(project.get('status') or ''))} "
                "（確定＝顧客合意／対応済み＝リフォーム完了）"
            )
            st.write(f"**必要人数**：{project.get('required_workers', '-')}")
            st.write(f"**作業時間（分）**：{project.get('work_duration_minutes', '-')}")
            st.write(f"**必要車両数**：{project.get('required_vehicle_count') or '-'}")
            st.write(f"**備考**：{project.get('note') or '-'}")
            ss = project.get("scheduled_start_at")
            se = project.get("scheduled_end_at")
            if ss or se:
                left = _format_scheduled_at(str(ss)) if ss else "—"
                right = _format_scheduled_at(str(se)) if se else "—"
                st.write(f"**予定日時**：{left} 〜 {right}")
            else:
                st.write("**予定日時**：未登録（候補検索で確定すると表示されます）")

            _refs = project.get("google_calendar_event_refs") or []
            _has_refs = isinstance(_refs, list) and len(_refs) > 0
            if bool(str(ss or "").strip()) or bool(str(se or "").strip()) or _has_refs:
                pid = str(project.get("project_id") or "")
                if st.button(
                    "予定を取り消す（Googleカレンダー削除＋案件の予定日時もクリア）",
                    key=f"project_clear_schedule_{pid}",
                ):
                    try:
                        wk = list_workers()
                        vc = list_vehicles()
                        try:
                            stg = get_settings()
                        except FirestoreConnectionError:
                            stg = {}
                        gcal_tok = st.session_state.get("google_calendar_tokens") or {}
                        vf_sess = gcal_tok.get("vehicle_fleet") if isinstance(gcal_tok, dict) else None
                        msgs_c, ok_c = remove_project_schedule_from_google(
                            project=project,
                            workers=wk,
                            vehicles=vc,
                            session_tokens=st.session_state.get("google_calendar_tokens"),
                            settings=stg,
                            vehicle_fleet_session=vf_sess,
                            current_user_name=st.session_state.get("current_user_name"),
                        )
                    except Exception as exc:
                        st.error("予定の取り消し中にエラーが発生しました。")
                        st.exception(exc)
                    else:
                        for m in msgs_c:
                            st.info(m)
                        if ok_c:
                            if "project_detail_id" in st.session_state:
                                del st.session_state["project_detail_id"]
                            if edit_key in st.session_state:
                                del st.session_state[edit_key]
                            st.rerun()

            col_edit, col_del, col_close = st.columns(3)
            with col_edit:
                if st.button("編集", key="project_dialog_edit"):
                    st.session_state[edit_key] = True
                    st.rerun()
            with col_del:
                if st.button("削除", key="project_dialog_delete"):
                    st.session_state["pending_delete_project_id"] = str(project.get("project_id") or "")
                    st.session_state.pop("project_detail_id", None)
                    st.rerun()
            with col_close:
                if st.button("閉じる", key="project_dialog_close"):
                    if "project_detail_id" in st.session_state:
                        del st.session_state["project_detail_id"]
                    if edit_key in st.session_state:
                        del st.session_state[edit_key]
                    st.rerun()
        else:
            # 編集モード
            with st.form(key="project_edit_form"):
                edit_project_name = st.text_input("案件名*", value=project.get("project_name", ""), key="edit_project_name")
                edit_customer_name = st.text_input("顧客名*", value=project.get("customer_name", ""), key="edit_customer_name")
                edit_address = st.text_input("住所*", value=project.get("address", ""), key="edit_address")
                ct_raw = project.get("construction_type")
                ct_selected = ct_raw if isinstance(ct_raw, list) else ([ct_raw] if ct_raw else [])
                edit_construction_type = []
                st.write("施工内容*（複数選択可）")
                for opt in CONSTRUCTION_TYPE_OPTIONS:
                    if st.checkbox(opt, value=opt in ct_selected, key=f"edit_ct_{project.get('project_id')}_{opt}"):
                        edit_construction_type.append(opt)
                edit_construction_type_other = st.text_input(
                    "施工内容詳細（「その他」選択時のみ入力）",
                    value=project.get("construction_type_other") or "",
                    key="edit_construction_type_other",
                    placeholder="「その他」を選択した場合のみ入力",
                )
                edit_required_workers = st.number_input(
                    "必要人数*",
                    min_value=1,
                    value=int(project.get("required_workers", 1)),
                    key="edit_required_workers",
                )
                edit_work_duration = st.number_input(
                    "作業時間（分）*",
                    min_value=60,
                    step=30,
                    value=int(project.get("work_duration_minutes", 120)),
                    key="edit_work_duration",
                )
                edit_required_vehicle_count = st.number_input(
                    "必要車両数",
                    min_value=0,
                    value=int(project.get("required_vehicle_count") or 0),
                    key="edit_required_vehicle_count",
                )
                edit_note = st.text_area("備考", value=project.get("note") or "", key="edit_note")
                _status_keys = list(STATUS_LABELS.keys())
                _cur_st = str(project.get("status") or "draft")
                _st_idx = _status_keys.index(_cur_st) if _cur_st in _status_keys else 0
                edit_status = st.selectbox(
                    "ステータス",
                    options=_status_keys,
                    format_func=lambda k: STATUS_LABELS.get(k, k),
                    index=_st_idx,
                    key=f"edit_status_{project.get('project_id')}",
                    help="確定＝顧客合意。対応済み＝リフォーム完了（候補検索の案件一覧に表示されません）。",
                )

                col_save, col_cancel = st.columns(2)
                with col_save:
                    submitted = st.form_submit_button("更新")
                with col_cancel:
                    cancel_clicked = st.form_submit_button("キャンセル")

            if cancel_clicked:
                st.session_state[edit_key] = False
                st.rerun()

            if submitted:
                form_values = {
                    "project_name": edit_project_name,
                    "customer_name": edit_customer_name,
                    "address": edit_address,
                    "construction_type": edit_construction_type,
                    "construction_type_other": edit_construction_type_other if CONSTRUCTION_TYPE_OTHER in edit_construction_type else "",
                    "required_workers": edit_required_workers,
                    "work_duration_minutes": edit_work_duration,
                    "required_vehicle_count": edit_required_vehicle_count,
                    "note": edit_note,
                    "status": edit_status,
                }
                is_valid, errors = validate_project_input(form_values)
                if not is_valid:
                    st.error("必須未入力または不正な値があります。")
                    for msg in errors:
                        st.write(f"- {msg}")
                else:
                    try:
                        updated = update_project(
                            project["project_id"],
                            form_values,
                            current_user_name=st.session_state.get("current_user_name"),
                        )
                        if updated:
                            st.success("案件を更新しました。")
                            if edit_key in st.session_state:
                                del st.session_state[edit_key]
                            if "project_detail_id" in st.session_state:
                                del st.session_state["project_detail_id"]
                            st.rerun()
                        else:
                            st.error("データ未登録または更新対象が見つかりません。")
                    except FirestoreSaveError as e:
                        st.error(f"更新に失敗しました。{e}")
                    except FirestoreConnectionError:
                        st.error(DB_UNAVAILABLE_MESSAGE)
                    except Exception as exc:
                        st.error("想定外エラーが発生しました。")
                        st.exception(exc)

    _show_project_detail()


def render_page() -> None:
    """案件一覧画面（案件登録・案件詳細を集約）."""
    st.set_page_config(
        page_title=f"{APP_TITLE} - 案件一覧",
        layout="wide",
        menu_items=STREAMLIT_MENU_ITEMS,
    )
    init_session_state()
    inject_wide_layout()
    inject_sidebar_nav()

    if st.session_state.pop("project_deleted_flash", False):
        st.success("削除しました。")

    st.title("案件一覧")
    st.caption("案件の登録・一覧・詳細を管理します。")

    if st.session_state.pop("register_done_flash", False):
        st.success("案件を登録しました。")

    # ----------------------------
    # 上部：新規登録フォーム
    # ----------------------------
    st.subheader("新規登録")
    with st.expander("案件を新規登録", expanded=False):
        st.write("施工内容*（複数選択可）")
        new_construction_type = []
        for opt in CONSTRUCTION_TYPE_OPTIONS:
            if st.checkbox(opt, key=f"new_ct_{opt}"):
                new_construction_type.append(opt)
        if CONSTRUCTION_TYPE_OTHER in new_construction_type:
            new_construction_type_other = st.text_input(
                "施工内容詳細*",
                key="new_construction_type_other",
                placeholder="自由入力",
            )
        else:
            new_construction_type_other = ""

        with st.form(key="project_register_form"):
            col_left, col_right = st.columns(2)
            with col_left:
                new_project_name = st.text_input("案件名*", key="new_project_name")
                new_customer_name = st.text_input("顧客名*", key="new_customer_name")
                new_address = st.text_input("住所*", key="new_address")

            with col_right:
                new_work_duration = st.number_input(
                    "作業時間（分）*",
                    min_value=0,
                    step=30,
                    key="new_work_duration_minutes",
                )
                new_required_workers = st.number_input(
                    "必要人数*",
                    min_value=0,
                    step=1,
                    key="new_required_workers",
                )
                new_required_vehicle_count = st.number_input(
                    "必要車両数（任意）",
                    min_value=0,
                    step=1,
                    key="new_required_vehicle_count",
                )

            new_note = st.text_area("備考", key="new_note")

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
                        with st.spinner("登録中…"):
                            create_project(
                                form_values,
                                current_user_name=st.session_state.get("current_user_name"),
                            )
                        st.session_state["register_done_flash"] = True
                        st.rerun()
                    except FirestoreSaveError as e:
                        st.error(f"保存に失敗しました。{e}")
                    except FirestoreConnectionError:
                        st.error(DB_UNAVAILABLE_MESSAGE)
                    except Exception as exc:
                        st.error("想定外エラーが発生しました。")
                        st.exception(exc)

    # ----------------------------
    # 絞り込み条件（案件は常時表示、条件で絞り込み）
    # ----------------------------
    st.subheader("絞り込み")
    col1, col2, col3, col_btn = st.columns([2, 2, 2, 1])
    with col1:
        filter_project_name = st.text_input(
            "案件名（部分一致）",
            key="filter_project_name",
            placeholder="入力で絞り込み",
            on_change=_clear_detail_on_filter_change,
        )
    with col2:
        filter_customer_name = st.text_input(
            "顧客名（部分一致）",
            key="filter_customer_name",
            placeholder="入力で絞り込み",
            on_change=_clear_detail_on_filter_change,
        )
    with col3:
        filter_status = st.selectbox(
            "ステータス",
            options=[""] + list(STATUS_LABELS.keys()),
            format_func=lambda v: STATUS_LABELS.get(v, "") if v else "（全て）",
            key="filter_status",
            on_change=_clear_detail_on_filter_change,
        )
    with col_btn:
        st.write("")
        st.write("")
        if st.button("クリア", key="filter_clear"):
            for k in ("filter_project_name", "filter_customer_name", "filter_status"):
                if k in st.session_state:
                    del st.session_state[k]
            st.session_state.pop("project_detail_id", None)
            st.rerun()

    # 全案件を取得し、絞り込み条件でフィルタ
    try:
        all_projects = list_projects({})
    except FirestoreConnectionError:
        st.error(DB_UNAVAILABLE_MESSAGE)
        return
    except Exception as exc:
        st.error("案件一覧の取得中に想定外エラーが発生しました。")
        st.exception(exc)
        return

    filters = {
        "project_name": (filter_project_name or "").strip(),
        "customer_name": (filter_customer_name or "").strip(),
        "status": (filter_status or "").strip(),
    }
    projects = list_projects(filters)

    # 削除確認ダイアログ
    pending_del = st.session_state.get("pending_delete_project_id")
    if pending_del:
        pname = "-"
        for p in all_projects:
            if str(p.get("project_id")) == str(pending_del):
                pname = str(p.get("project_name") or pending_del)
                break
        _render_delete_dialog(str(pending_del), pname)

    # ----------------------------
    # 案件詳細ポップアップ（クリック時に表示）
    # ----------------------------
    project_detail_id = st.session_state.get("project_detail_id")
    if project_detail_id and all_projects:
        target = next((p for p in all_projects if p.get("project_id") == project_detail_id), None)
        if target is not None:
            _render_project_detail_dialog(target)

    # ----------------------------
    # 案件リスト（常時表示、クリックでポップアップ）
    # ----------------------------
    st.subheader("案件リスト（クリックで詳細表示）")
    if not all_projects:
        st.info("登録されている案件がありません。上部の「案件を新規登録」から登録してください。")
    elif not projects:
        st.warning("絞り込み条件に該当する案件がありません。")
    else:
        for p in projects:
            status = format_status(p.get("status", ""))
            has_schedule = bool(str(p.get("scheduled_start_at") or "").strip())
            schedule_label = "有" if has_schedule else "無"
            with st.container():
                col_info, col_btn = st.columns([4, 1])
                with col_info:
                    addr = str(p.get("address", "-"))
                    addr_short = (addr[:30] + "…") if len(addr) > 30 else addr
                    st.markdown(
                        f"**{p.get('project_name', '-')}** | "
                        f"{p.get('customer_name', '-')} | "
                        f"{addr_short} | "
                        f"ステータス: {status} | "
                        f"日程調整: **{schedule_label}**"
                    )
                with col_btn:
                    if st.button("詳細", key=f"detail_{p.get('project_id')}"):
                        st.session_state["project_detail_id"] = p.get("project_id")
                        st.rerun()
                st.divider()


if __name__ == "__main__":
    render_page()
