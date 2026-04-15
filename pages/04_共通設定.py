from __future__ import annotations

import streamlit as st

from services.firestore_service import FirestoreConnectionError, FirestoreSaveError
from services.setting_service import get_settings, reset_to_defaults, save_settings
from services.vehicle_service import (
    VEHICLE_STATUS,
    create_vehicle,
    deactivate_vehicle,
    delete_vehicle,
    list_vehicles,
    update_vehicle,
)
from services.google_oauth_service import (
    build_authorization_url,
    oauth_client_configured,
)
from services.email_service import (
    build_vehicle_item_oauth_email_body,
    build_vehicle_item_oauth_email_html,
    build_worker_oauth_email_body,
    build_worker_oauth_email_html,
    send_plain_email,
    smtp_configured,
)
from services.worker_service import create_worker, deactivate_worker, delete_worker, list_workers, update_worker
from config.constants import APP_TITLE, DB_UNAVAILABLE_MESSAGE
from utils.layout_util import STREAMLIT_MENU_ITEMS, inject_sidebar_nav, inject_wide_layout
from utils.session_util import init_session_state


@st.dialog("職人の削除")
def _confirm_delete_worker_dialog(worker_id: str, worker_name: str) -> None:
    st.write(f"「{worker_name}」を削除しますか？この操作は取り消せません。")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("はい", type="primary", key=f"wdlg_yes_{worker_id}"):
            try:
                if delete_worker(worker_id):
                    st.session_state.pop("worker_delete_dialog_id", None)
                    st.session_state.pop("worker_delete_dialog_name", None)
                    st.rerun()
                else:
                    st.error("削除対象が見つかりませんでした。")
            except (FirestoreSaveError, FirestoreConnectionError) as e:
                st.error(f"削除に失敗しました。{e}")
    with c2:
        if st.button("いいえ", key=f"wdlg_no_{worker_id}"):
            st.session_state.pop("worker_delete_dialog_id", None)
            st.session_state.pop("worker_delete_dialog_name", None)
            st.rerun()


@st.dialog("車両の削除")
def _confirm_delete_vehicle_dialog(vehicle_id: str, vehicle_name: str) -> None:
    st.write(f"「{vehicle_name}」を削除しますか？この操作は取り消せません。")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("はい", type="primary", key=f"vdlg_yes_{vehicle_id}"):
            try:
                if delete_vehicle(vehicle_id):
                    st.session_state.pop("vehicle_delete_dialog_id", None)
                    st.session_state.pop("vehicle_delete_dialog_name", None)
                    st.rerun()
                else:
                    st.error("削除対象が見つかりませんでした。")
            except (FirestoreSaveError, FirestoreConnectionError) as e:
                st.error(f"削除に失敗しました。{e}")
    with c2:
        if st.button("いいえ", key=f"vdlg_no_{vehicle_id}"):
            st.session_state.pop("vehicle_delete_dialog_id", None)
            st.session_state.pop("vehicle_delete_dialog_name", None)
            st.rerun()


def _render_common_settings_tab() -> None:
    """共通設定タブ（表示→編集ボタンで編集モードへ）."""
    try:
        settings = get_settings()
    except FirestoreConnectionError:
        st.error(DB_UNAVAILABLE_MESSAGE)
        return
    except Exception as exc:
        st.error("設定の取得中に想定外エラーが発生しました。")
        st.exception(exc)
        return

    is_edit_mode = st.session_state.get("common_settings_edit_mode", False)

    if not is_edit_mode:
        # 表示モード
        st.subheader("基本設定")
        st.write(f"**会社住所**：{settings.get('office_address', '-')}")
        st.write(f"**積込時間（分）**：{settings.get('load_minutes', '-')}")
        st.write(
            f"**検索範囲日数（保存値・将来用）**：{settings.get('search_range_days', '-')}"
        )
        st.caption(
            "実カレンダー候補検索は、当日を含む週（日曜〜土曜）に限定しています。"
        )
        st.write(f"**候補刻み（分）**：{settings.get('time_slot_minutes', '-')}")
        st.write(
            f"**候補最大件数（保存値・将来用）**：{settings.get('max_candidate_count', '-')}"
        )
        st.caption("候補検索は条件を満たす空き枠をすべて列挙します（件数上限はかけていません）。")
        ranks = settings.get("worker_ranks") or []
        if isinstance(ranks, list):
            st.write(f"**職人ランク候補**：{'、'.join(str(x) for x in ranks if str(x).strip()) or '-'}")

        st.subheader("就業時間（候補検索）")
        st.write(
            f"**開始〜終了**：{settings.get('work_hours_start', '-')} 〜 {settings.get('work_hours_end', '-')}"
        )
        st.caption("この時間帯に収まる枠だけを候補として検索します。")
        st.write(
            f"**移動再計算モード（候補確定時）**：{'有効' if settings.get('recalc_travel_on_commit', False) else '無効'}"
        )
        st.caption("有効時は、候補確定日の移動予定（[移動]）を再計算して張り直します。")

        st.subheader("渋滞バッファ（朝）")
        st.write(f"**朝バッファ（分）**：{settings.get('traffic_buffer_morning_minutes', '-')}")
        st.write(f"**朝開始〜終了時刻**：{settings.get('traffic_buffer_morning_start', '-')} 〜 {settings.get('traffic_buffer_morning_end', '-')}")
        st.caption("渋滞バッファ用の時間帯です。候補検索の終了時刻ではありません（終了は上の就業時間）。")

        st.subheader("渋滞バッファ（夕）")
        st.write(f"**夕バッファ（分）**：{settings.get('traffic_buffer_evening_minutes', '-')}")
        st.write(f"**夕開始〜終了時刻**：{settings.get('traffic_buffer_evening_start', '-')} 〜 {settings.get('traffic_buffer_evening_end', '-')}")

        col_edit, col_reset = st.columns(2)
        with col_edit:
            if st.button("編集", key="common_settings_edit_btn"):
                st.session_state["common_settings_edit_mode"] = True
                st.rerun()
        with col_reset:
            if st.button("初期値に戻す", key="common_settings_reset_btn"):
                try:
                    reset_to_defaults()
                    st.success("初期値に戻しました。")
                    st.rerun()
                except FirestoreSaveError as e:
                    st.error(f"リセットに失敗しました。{e}")
                except FirestoreConnectionError:
                    st.error(DB_UNAVAILABLE_MESSAGE)
                except Exception as exc:
                    st.error("想定外エラーが発生しました。")
                    st.exception(exc)
    else:
        # 編集モード
        with st.form(key="settings_form"):
            st.subheader("基本設定")
            office_address = st.text_input(
                "会社住所*",
                value=settings.get("office_address", ""),
                key="form_office_address",
            )
            load_minutes = st.number_input(
                "積込時間（分）*",
                min_value=0,
                step=5,
                value=int(settings.get("load_minutes", 20)),
                key="form_load_minutes",
            )
            search_range_days = st.number_input(
                "検索範囲日数（将来用・現状は未使用）*",
                min_value=1,
                step=1,
                value=int(settings.get("search_range_days", 90)),
                key="form_search_range_days",
                help="候補検索の実処理は当日を含む1週間（日曜始まり）です。",
            )
            time_slot_minutes = st.number_input(
                "候補刻み（分）*",
                min_value=5,
                step=5,
                value=int(settings.get("time_slot_minutes", 30)),
                key="form_time_slot_minutes",
            )
            max_candidate_count = st.number_input(
                "候補最大件数（将来用・現状は未使用）*",
                min_value=1,
                step=1,
                value=int(settings.get("max_candidate_count", 20)),
                key="form_max_candidate_count",
                help="検索結果は現状、上限なくすべて表示します。",
            )
            ranks_raw = settings.get("worker_ranks") or []
            if isinstance(ranks_raw, list):
                ranks_text_default = "\n".join(str(x) for x in ranks_raw if str(x).strip())
            else:
                ranks_text_default = str(ranks_raw or "")
            worker_ranks_text = st.text_area(
                "職人ランク候補（1行1つ）",
                value=ranks_text_default,
                key="form_worker_ranks",
                help="候補検索のランク絞り込み・職人マスタ登録に使う候補値です。",
            )

            st.subheader("就業時間（候補検索）")
            col_wh1, col_wh2 = st.columns(2)
            with col_wh1:
                work_hours_start = st.text_input(
                    "就業開始*",
                    value=settings.get("work_hours_start", "07:00"),
                    key="form_work_hours_start",
                    help="HH:MM（例: 07:00）。この時間以降の枠を候補に含めます。",
                )
            with col_wh2:
                work_hours_end = st.text_input(
                    "就業終了*",
                    value=settings.get("work_hours_end", "19:00"),
                    key="form_work_hours_end",
                    help="HH:MM（例: 19:00）。候補の終了時刻がこの時間を超えないようにします。",
                )
            recalc_travel_on_commit = st.checkbox(
                "候補確定時に当日の移動経路を再計算する",
                value=bool(settings.get("recalc_travel_on_commit", False)),
                key="form_recalc_travel_on_commit",
                help="割当対象の職人・車両カレンダーで、当日の [移動] 予定を再計算して再登録します。",
            )

            st.subheader("渋滞バッファ（朝）")
            col_m1, col_m2 = st.columns(2)
            with col_m1:
                traffic_buffer_morning_minutes = st.number_input(
                    "朝バッファ（分）*",
                    min_value=0,
                    step=5,
                    value=int(settings.get("traffic_buffer_morning_minutes", 20)),
                    key="form_traffic_buffer_morning_minutes",
                )
            with col_m2:
                traffic_buffer_morning_start = st.text_input(
                    "朝開始時刻*",
                    value=settings.get("traffic_buffer_morning_start", "07:00"),
                    key="form_traffic_buffer_morning_start",
                )
            traffic_buffer_morning_end = st.text_input(
                "朝終了時刻*",
                value=settings.get("traffic_buffer_morning_end", "10:00"),
                key="form_traffic_buffer_morning_end",
            )

            st.subheader("渋滞バッファ（夕）")
            col_e1, col_e2 = st.columns(2)
            with col_e1:
                traffic_buffer_evening_minutes = st.number_input(
                    "夕バッファ（分）*",
                    min_value=0,
                    step=5,
                    value=int(settings.get("traffic_buffer_evening_minutes", 20)),
                    key="form_traffic_buffer_evening_minutes",
                )
            with col_e2:
                traffic_buffer_evening_start = st.text_input(
                    "夕開始時刻*",
                    value=settings.get("traffic_buffer_evening_start", "16:00"),
                    key="form_traffic_buffer_evening_start",
                )
            traffic_buffer_evening_end = st.text_input(
                "夕終了時刻*",
                value=settings.get("traffic_buffer_evening_end", "19:00"),
                key="form_traffic_buffer_evening_end",
            )

            col_save, col_cancel = st.columns(2)
            with col_save:
                submit_save = st.form_submit_button("更新")
            with col_cancel:
                submit_cancel = st.form_submit_button("キャンセル")

        if submit_cancel:
            st.session_state["common_settings_edit_mode"] = False
            st.rerun()

        if submit_save:
            try:
                save_settings(
                    {
                        "office_address": office_address,
                        "load_minutes": load_minutes,
                        "search_range_days": search_range_days,
                        "time_slot_minutes": time_slot_minutes,
                        "max_candidate_count": max_candidate_count,
                        "worker_ranks": [
                            r.strip()
                            for r in str(worker_ranks_text).splitlines()
                            if r.strip()
                        ],
                        "work_hours_start": work_hours_start,
                        "work_hours_end": work_hours_end,
                        "recalc_travel_on_commit": recalc_travel_on_commit,
                        "traffic_buffer_morning_minutes": traffic_buffer_morning_minutes,
                        "traffic_buffer_morning_start": traffic_buffer_morning_start,
                        "traffic_buffer_morning_end": traffic_buffer_morning_end,
                        "traffic_buffer_evening_minutes": traffic_buffer_evening_minutes,
                        "traffic_buffer_evening_start": traffic_buffer_evening_start,
                        "traffic_buffer_evening_end": traffic_buffer_evening_end,
                    }
                )
                st.success("設定を保存しました。")
                st.session_state["common_settings_edit_mode"] = False
                st.rerun()
            except FirestoreSaveError as e:
                st.error(f"保存に失敗しました。{e}")
            except FirestoreConnectionError:
                st.error(DB_UNAVAILABLE_MESSAGE)
            except Exception as exc:
                st.error("想定外エラーが発生しました。")
                st.exception(exc)


def _render_worker_tab() -> None:
    """職人マスタタブ（表示→編集/新規追加ボタンで編集モードへ）."""
    try:
        workers = list_workers()
        settings = get_settings()
    except FirestoreConnectionError:
        st.error(DB_UNAVAILABLE_MESSAGE)
        return
    except Exception as exc:
        st.error("職人一覧の取得中に想定外エラーが発生しました。")
        st.exception(exc)
        return
    ranks_cfg_raw = settings.get("worker_ranks") or []
    rank_options = [str(x).strip() for x in ranks_cfg_raw if str(x).strip()] if isinstance(ranks_cfg_raw, list) else []

    with st.expander("Google カレンダー連携（職人・OAuth）", expanded=False):
        st.markdown(
            "各職人の Google カレンダーを連携すると、空き時間の確認や予定の登録が行えます。"
            "本人の「〇〇で連携」から進み、表示された画面で許可してください。"
        )
        st.caption("うまくいかない場合は、担当の情報システムまたは管理者へお問い合わせください。")
        if oauth_client_configured():
            st.caption("メールアドレスを登録した職人には、「メールで送る」から同じ手順の案内を送れます。")
            if smtp_configured():
                st.caption(
                    "「メールで送る」は、職人マスタに **メールアドレス** を登録した職人に OAuth 用リンクを送ります。"
                )
            else:
                st.info("メールでリンクを送るには、メール送信のサーバー設定が必要です。管理者にお問い合わせください。")
            for w in workers:
                if not w.get("is_active"):
                    continue
                url = build_authorization_url(state=str(w["worker_id"]))
                if not url:
                    continue
                wmail = (w.get("email") or "").strip()
                mail_ok = smtp_configured() and bool(wmail)
                row1, row2, row3 = st.columns([2, 1, 1])
                with row1:
                    st.link_button(
                        f'{w.get("name", w["worker_id"])} で連携',
                        url,
                    )
                with row2:
                    linked = bool(w.get("google_refresh_token"))
                    st.caption("カレンダー連携済" if linked else "未連携")
                with row3:
                    if st.button(
                        "メールで送る",
                        key=f"oauth_mail_{w['worker_id']}",
                        disabled=not mail_ok,
                    ):
                        subj = "[日程調整] Google カレンダー連携のお願い"
                        wname = str(w.get("name") or w["worker_id"])
                        body = build_worker_oauth_email_body(wname, url)
                        html_body = build_worker_oauth_email_html(wname, url)
                        ok, err_msg = send_plain_email(wmail, subj, body, html_body=html_body)
                        if ok:
                            st.success(f"{wmail} に送信しました。")
                        else:
                            st.error(f"送信に失敗しました: {err_msg}")
                    elif smtp_configured() and not wmail:
                        st.caption("メール未登録")
        else:
            st.info("カレンダー連携は、サーバー側の設定が完了するまで利用できません。管理者にお問い合わせください。")

    edit_worker_id = st.session_state.get("worker_edit_id", None)  # None=表示, "__new__"=新規, "W001"=編集

    if edit_worker_id is None:
        # 表示モード
        st.subheader("職人一覧")
        if not workers:
            st.info("職人が登録されていません。")
        else:
            for w in workers:
                _rank = str(w.get("rank") or "").strip()
                _rank_badge = f"（ランク:{_rank}）" if _rank else "（ランク未設定）"
                with st.expander(
                    f"{w.get('worker_id')} - {w.get('name')}{_rank_badge} "
                    f"{'(無効)' if not w.get('is_active') else ''}"
                ):
                    col1, col2, col_btn = st.columns([2, 2, 1])
                    with col1:
                        st.write(f"**職人ID**: {w.get('worker_id')}")
                        st.write(f"**職人名**: {w.get('name')}")
                        st.write(f"**ランク**: {w.get('rank') or '-'}")
                        st.write(f"**メール**: {w.get('email') or '-'}")
                        st.write(f"**GoogleカレンダーID**: {w.get('calendar_id')}")
                    with col2:
                        st.write(f"**利用中**: {'有効' if w.get('is_active') else '無効'}")
                        st.write(f"**表示順**: {w.get('display_order')}")
                        st.write(f"**備考**: {w.get('note') or '-'}")
                    with col_btn:
                        if st.button("編集", key=f"edit_worker_{w.get('worker_id')}"):
                            st.session_state["worker_edit_id"] = w["worker_id"]
                            st.rerun()
                        if w.get("is_active") and st.button("無効化", key=f"deactivate_worker_{w.get('worker_id')}"):
                            try:
                                deactivate_worker(w["worker_id"])
                                st.rerun()
                            except (FirestoreSaveError, FirestoreConnectionError) as e:
                                st.error(f"無効化に失敗しました。{e}")
                        if st.button("削除", key=f"delete_worker_{w.get('worker_id')}"):
                            st.session_state["worker_delete_dialog_id"] = w["worker_id"]
                            st.session_state["worker_delete_dialog_name"] = str(w.get("name") or w["worker_id"])
                            st.rerun()

        if st.button("新規追加", key="worker_new_btn"):
            st.session_state["worker_edit_id"] = "__new__"
            st.rerun()
    else:
        # 編集モード（新規 or 既存）
        existing = next((w for w in workers if w["worker_id"] == edit_worker_id), None)
        st.subheader("新規追加" if edit_worker_id == "__new__" else f"編集 - {existing.get('worker_id')}")

        with st.form(key="worker_form"):
            name = st.text_input("職人名*", value=existing.get("name", "") if existing else "", key="worker_name")
            existing_rank = str(existing.get("rank", "") if existing else "").strip()
            rank_select_options = [""] + rank_options
            if existing_rank and existing_rank not in rank_select_options:
                rank_select_options.append(existing_rank)
            rank_default_idx = rank_select_options.index(existing_rank) if existing_rank in rank_select_options else 0
            rank = st.selectbox(
                "ランク",
                options=rank_select_options,
                index=rank_default_idx,
                format_func=lambda v: v if v else "（未設定）",
                key="worker_rank",
            )
            email = st.text_input(
                "メール（OAuth 案内用・任意）",
                value=existing.get("email", "") if existing else "",
                key="worker_email",
                placeholder="例: taro@example.com",
            )
            calendar_id = st.text_input("GoogleカレンダーID*", value=existing.get("calendar_id", "") if existing else "", key="worker_calendar_id")
            is_active = st.checkbox("利用中", value=existing.get("is_active", True) if existing else True, key="worker_is_active")
            display_order = st.number_input("表示順", min_value=0, value=existing.get("display_order", 0) if existing else 0, key="worker_display_order")
            note = st.text_area("備考", value=existing.get("note", "") if existing else "", key="worker_note")

            send_oauth_on_create = False
            if edit_worker_id == "__new__":
                send_oauth_on_create = st.checkbox(
                    "保存後、OAuth 連携案内メールを上記メール宛に送る",
                    value=True,
                    key="worker_send_oauth_on_create",
                )
                st.caption(
                    "オフにした場合は、職人マスタ上部の「Google カレンダー連携」から「メールで送る」でいつでも送信できます。"
                )

            col_save, col_cancel = st.columns(2)
            with col_save:
                submitted = st.form_submit_button("保存")
            with col_cancel:
                cancel_clicked = st.form_submit_button("キャンセル")

        if cancel_clicked:
            del st.session_state["worker_edit_id"]
            st.rerun()

        if submitted:
            if not name or not calendar_id:
                st.error("職人名とGoogleカレンダーIDは必須です。")
            else:
                try:
                    data = {
                        "name": name,
                        "rank": str(rank or "").strip(),
                        "email": (email or "").strip(),
                        "calendar_id": calendar_id,
                        "is_active": is_active,
                        "display_order": display_order,
                        "note": note,
                    }
                    if edit_worker_id == "__new__":
                        created = create_worker(data)
                        extra = ""
                        em = (data.get("email") or "").strip()
                        if send_oauth_on_create and em:
                            if not oauth_client_configured():
                                st.warning(
                                    "職人は追加しました。カレンダー連携のサーバー設定が未完了のため、案内メールは送れませんでした。"
                                )
                            elif not smtp_configured():
                                st.warning(
                                    "職人は追加しました。SMTP（.env）が未設定のため、案内メールは送れませんでした。"
                                )
                            else:
                                url = build_authorization_url(state=str(created["worker_id"]))
                                if url:
                                    subj = "[日程調整] Google カレンダー連携のお願い"
                                    wname = str(data.get("name") or created["worker_id"])
                                    body = build_worker_oauth_email_body(wname, url)
                                    html_body = build_worker_oauth_email_html(wname, url)
                                    ok, err_msg = send_plain_email(em, subj, body, html_body=html_body)
                                    if ok:
                                        extra = " 案内メールを送信しました。"
                                    else:
                                        st.warning(
                                            f"職人は追加しましたが、案内メールの送信に失敗しました: {err_msg}"
                                        )
                                else:
                                    st.warning(
                                        "職人は追加しましたが、認証 URL を生成できなかったためメールは送れませんでした。"
                                    )
                        elif send_oauth_on_create and not em:
                            st.warning(
                                "職人は追加しました。案内メールを送るにはメールアドレスの入力が必要です。"
                            )
                        st.success("職人を追加しました。" + extra)
                    else:
                        update_worker(edit_worker_id, data)
                        st.success("職人を更新しました。")
                    del st.session_state["worker_edit_id"]
                    st.rerun()
                except FirestoreSaveError as e:
                    st.error(f"保存に失敗しました。{e}")
                except FirestoreConnectionError:
                    st.error(DB_UNAVAILABLE_MESSAGE)
                except Exception as exc:
                    st.error("想定外エラーが発生しました。")
                    st.exception(exc)

    wid_dlg = st.session_state.get("worker_delete_dialog_id")
    if wid_dlg:
        _confirm_delete_worker_dialog(
            wid_dlg,
            st.session_state.get("worker_delete_dialog_name", ""),
        )


def _render_vehicle_tab() -> None:
    """車両マスタタブ（表示→編集/新規追加ボタンで編集モードへ）."""
    try:
        vehicles = list_vehicles()
    except FirestoreConnectionError:
        st.error(DB_UNAVAILABLE_MESSAGE)
        return
    except Exception as exc:
        st.error("車両一覧の取得中に想定外エラーが発生しました。")
        st.exception(exc)
        return

    with st.expander("Google カレンダー連携（車両・OAuth）", expanded=False):
        st.markdown(
            "**各車両**の Google カレンダーを、車両ごとに連携します（職人と同じ考え方です）。"
            "新しい車両を追加したら、その車両の行から連携してください。"
        )
        st.caption("うまくいかない場合は、担当の情報システムまたは管理者へお問い合わせください。")
        if oauth_client_configured():
            if smtp_configured():
                st.caption(
                    "「メールで送る」は、車両マスタに **メールアドレス** を登録した車両に OAuth 用リンクを送ります。"
                )
            else:
                st.info("メールでリンクを送るには、メール送信のサーバー設定が必要です。管理者にお問い合わせください。")
            for v in vehicles:
                if not v.get("is_active"):
                    continue
                url = build_authorization_url(state=str(v["vehicle_id"]))
                if not url:
                    continue
                vmail = (v.get("email") or "").strip()
                mail_ok = smtp_configured() and bool(vmail)
                row1, row2, row3 = st.columns([2, 1, 1])
                with row1:
                    st.link_button(
                        f'{v.get("name", v["vehicle_id"])} で連携',
                        url,
                    )
                with row2:
                    linked = bool(v.get("google_refresh_token"))
                    st.caption("カレンダー連携済" if linked else "未連携")
                with row3:
                    if st.button(
                        "メールで送る",
                        key=f"oauth_mail_vehicle_{v['vehicle_id']}",
                        disabled=not mail_ok,
                    ):
                        subj = "[日程調整] 車両 Google カレンダー連携のお願い"
                        vname = str(v.get("name") or v["vehicle_id"])
                        body = build_vehicle_item_oauth_email_body(vname, url)
                        html_body = build_vehicle_item_oauth_email_html(vname, url)
                        ok, err_msg = send_plain_email(vmail, subj, body, html_body=html_body)
                        if ok:
                            st.success(f"{vmail} に送信しました。")
                        else:
                            st.error(f"送信に失敗しました: {err_msg}")
                    elif smtp_configured() and not vmail:
                        st.caption("メール未登録")
        else:
            st.info("カレンダー連携は、サーバー側の設定が完了するまで利用できません。管理者にお問い合わせください。")

    edit_vehicle_id = st.session_state.get("vehicle_edit_id", None)

    if edit_vehicle_id is None:
        # 表示モード
        st.subheader("車両一覧")
        if not vehicles:
            st.info("車両が登録されていません。")
        else:
            for v in vehicles:
                status_label = VEHICLE_STATUS.get(v.get("status"), v.get("status"))
                with st.expander(f"{v.get('vehicle_id')} - {v.get('name')} ({status_label}) {'(無効)' if not v.get('is_active') else ''}"):
                    col1, col2, col_btn = st.columns([2, 2, 1])
                    with col1:
                        st.write(f"**車両ID**: {v.get('vehicle_id')}")
                        st.write(f"**車両名**: {v.get('name')}")
                        st.write(f"**メール**: {v.get('email') or '-'}")
                        st.write(f"**定員**: {v.get('capacity')}人")
                        st.write(f"**GoogleカレンダーID**: {v.get('calendar_id')}")
                    with col2:
                        st.write(f"**利用中**: {'有効' if v.get('is_active') else '無効'}")
                        st.write(f"**状態**: {status_label}")
                        st.write(f"**表示順**: {v.get('display_order')}")
                        st.write(f"**備考**: {v.get('note') or '-'}")
                    with col_btn:
                        if st.button("編集", key=f"edit_vehicle_{v.get('vehicle_id')}"):
                            st.session_state["vehicle_edit_id"] = v["vehicle_id"]
                            st.rerun()
                        if v.get("is_active") and st.button("無効化", key=f"deactivate_vehicle_{v.get('vehicle_id')}"):
                            try:
                                deactivate_vehicle(v["vehicle_id"])
                                st.rerun()
                            except (FirestoreSaveError, FirestoreConnectionError) as e:
                                st.error(f"無効化に失敗しました。{e}")
                        if st.button("削除", key=f"delete_vehicle_{v.get('vehicle_id')}"):
                            st.session_state["vehicle_delete_dialog_id"] = v["vehicle_id"]
                            st.session_state["vehicle_delete_dialog_name"] = str(v.get("name") or v["vehicle_id"])
                            st.rerun()

        if st.button("新規追加", key="vehicle_new_btn"):
            st.session_state["vehicle_edit_id"] = "__new__"
            st.rerun()
    else:
        # 編集モード（新規 or 既存）
        existing = next((v for v in vehicles if v["vehicle_id"] == edit_vehicle_id), None)
        st.subheader("新規追加" if edit_vehicle_id == "__new__" else f"編集 - {existing.get('vehicle_id')}")

        with st.form(key="vehicle_form"):
            name = st.text_input("車両名*", value=existing.get("name", "") if existing else "", key="vehicle_name")
            email = st.text_input(
                "メール（OAuth 案内用・任意）",
                value=existing.get("email", "") if existing else "",
                key="vehicle_email",
                placeholder="例: fleet@example.com",
            )
            capacity = st.number_input("定員*", min_value=1, value=existing.get("capacity", 1) if existing else 1, key="vehicle_capacity")
            calendar_id = st.text_input("GoogleカレンダーID*", value=existing.get("calendar_id", "") if existing else "", key="vehicle_calendar_id")
            is_active = st.checkbox("利用中", value=existing.get("is_active", True) if existing else True, key="vehicle_is_active")
            status_options = list(VEHICLE_STATUS.keys())
            status_index = status_options.index(existing.get("status", "available")) if existing and existing.get("status") in status_options else 0
            status = st.selectbox("状態", options=status_options, format_func=lambda x: VEHICLE_STATUS.get(x, x), index=status_index, key="vehicle_status")
            display_order = st.number_input("表示順", min_value=0, value=existing.get("display_order", 0) if existing else 0, key="vehicle_display_order")
            note = st.text_area("備考", value=existing.get("note", "") if existing else "", key="vehicle_note")

            send_oauth_on_create_vehicle = False
            if edit_vehicle_id == "__new__":
                send_oauth_on_create_vehicle = st.checkbox(
                    "保存後、OAuth 連携案内メールを上記メール宛に送る",
                    value=True,
                    key="vehicle_send_oauth_on_create",
                )
                st.caption(
                    "オフにした場合は、車両マスタ上部の「Google カレンダー連携」から「メールで送る」でいつでも送信できます。"
                )

            col_save, col_cancel = st.columns(2)
            with col_save:
                submitted = st.form_submit_button("保存")
            with col_cancel:
                cancel_clicked = st.form_submit_button("キャンセル")

        if cancel_clicked:
            del st.session_state["vehicle_edit_id"]
            st.rerun()

        if submitted:
            if not name or not calendar_id:
                st.error("車両名とGoogleカレンダーIDは必須です。")
            else:
                try:
                    data = {
                        "name": name,
                        "email": (email or "").strip(),
                        "capacity": capacity,
                        "calendar_id": calendar_id,
                        "is_active": is_active,
                        "status": status,
                        "display_order": display_order,
                        "note": note,
                    }
                    if edit_vehicle_id == "__new__":
                        created_v = create_vehicle(data)
                        extra_v = ""
                        em_v = (data.get("email") or "").strip()
                        if send_oauth_on_create_vehicle and em_v:
                            if not oauth_client_configured():
                                st.warning(
                                    "車両は追加しました。カレンダー連携のサーバー設定が未完了のため、案内メールは送れませんでした。"
                                )
                            elif not smtp_configured():
                                st.warning(
                                    "車両は追加しました。SMTP（.env）が未設定のため、案内メールは送れませんでした。"
                                )
                            else:
                                url_v = build_authorization_url(state=str(created_v["vehicle_id"]))
                                if url_v:
                                    subj_v = "[日程調整] 車両 Google カレンダー連携のお願い"
                                    vn = str(data.get("name") or created_v["vehicle_id"])
                                    body_v = build_vehicle_item_oauth_email_body(vn, url_v)
                                    html_v = build_vehicle_item_oauth_email_html(vn, url_v)
                                    ok_v, err_v = send_plain_email(em_v, subj_v, body_v, html_body=html_v)
                                    if ok_v:
                                        extra_v = " 案内メールを送信しました。"
                                    else:
                                        st.warning(
                                            f"車両は追加しましたが、案内メールの送信に失敗しました: {err_v}"
                                        )
                                else:
                                    st.warning(
                                        "車両は追加しましたが、認証 URL を生成できなかったためメールは送れませんでした。"
                                    )
                        elif send_oauth_on_create_vehicle and not em_v:
                            st.warning(
                                "車両は追加しました。案内メールを送るにはメールアドレスの入力が必要です。"
                            )
                        st.success("車両を追加しました。" + extra_v)
                    else:
                        update_vehicle(edit_vehicle_id, data)
                        st.success("車両を更新しました。")
                    del st.session_state["vehicle_edit_id"]
                    st.rerun()
                except FirestoreSaveError as e:
                    st.error(f"保存に失敗しました。{e}")
                except FirestoreConnectionError:
                    st.error(DB_UNAVAILABLE_MESSAGE)
                except Exception as exc:
                    st.error("想定外エラーが発生しました。")
                    st.exception(exc)

    vid_dlg = st.session_state.get("vehicle_delete_dialog_id")
    if vid_dlg:
        _confirm_delete_vehicle_dialog(
            vid_dlg,
            st.session_state.get("vehicle_delete_dialog_name", ""),
        )


def render_page() -> None:
    """共通設定画面（タブ: 共通設定 / 職人マスタ / 車両マスタ）."""
    st.set_page_config(
        page_title=f"{APP_TITLE} - 共通設定",
        layout="wide",
        menu_items=STREAMLIT_MENU_ITEMS,
    )
    init_session_state()
    st.session_state["_active_page_id"] = "common_settings"
    inject_wide_layout()
    inject_sidebar_nav()

    st.title("共通設定")
    st.caption("システムの共通設定・職人マスタ・車両マスタを管理します。")

    tab1, tab2, tab3 = st.tabs(["共通設定", "職人マスタ", "車両マスタ"])
    with tab1:
        _render_common_settings_tab()
    with tab2:
        _render_worker_tab()
    with tab3:
        _render_vehicle_tab()


if __name__ == "__main__":
    render_page()
