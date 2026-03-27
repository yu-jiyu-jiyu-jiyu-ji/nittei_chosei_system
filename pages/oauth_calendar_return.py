"""Google OAuth リダイレクト先。GOOGLE_OAUTH_REDIRECT_URI にこのページの URL を登録する."""

from __future__ import annotations

import re

import streamlit as st

from services.google_oauth_service import (
    OAUTH_STATE_VEHICLE_FLEET,
    exchange_code_for_credentials,
    oauth_client_configured,
)
from services.setting_service import save_settings
from services.vehicle_service import update_vehicle
from services.worker_service import update_worker
from utils.layout_util import STREAMLIT_MENU_ITEMS, inject_wide_layout
from utils.session_util import init_session_state


def render_page() -> None:
    st.set_page_config(
        page_title="Googleカレンダー連携",
        layout="centered",
        menu_items=STREAMLIT_MENU_ITEMS,
    )
    init_session_state()
    inject_wide_layout()

    st.title("Googleカレンダー連携")

    if not oauth_client_configured():
        st.error("GOOGLE_OAUTH_CLIENT_ID / SECRET が .env に設定されていません。")
        return

    qp = st.query_params
    code = qp.get("code")
    state = qp.get("state")

    if code and state:
        creds, exch_err = exchange_code_for_credentials(str(code))
        if creds and creds.refresh_token:
            state_str = str(state).strip()
            if "google_calendar_tokens" not in st.session_state:
                st.session_state["google_calendar_tokens"] = {}

            if state_str == OAUTH_STATE_VEHICLE_FLEET:
                st.session_state["google_calendar_tokens"]["vehicle_fleet"] = {
                    "refresh_token": creds.refresh_token,
                }
                try:
                    save_settings({"google_vehicle_refresh_token": creds.refresh_token})
                    st.success("車両用 Google カレンダー（共通・フォールバック）の連携が完了しました。")
                except Exception:
                    st.warning(
                        "セッションに保存しましたが、Firestore への保存に失敗しました。"
                        "GOOGLE_APPLICATION_CREDENTIALS を確認するか、再試行してください。"
                    )
            elif re.fullmatch(r"V\d+", state_str):
                vid = state_str
                st.session_state["google_calendar_tokens"][vid] = {
                    "refresh_token": creds.refresh_token,
                }
                try:
                    update_vehicle(vid, {"google_refresh_token": creds.refresh_token})
                except Exception:
                    st.caption("※ Firestore へのトークン保存はスキップされました（未接続または権限）。")
                st.success(f"車両 {vid} の Google カレンダー連携が完了しました。")
            else:
                wid = state_str
                st.session_state["google_calendar_tokens"][wid] = {
                    "refresh_token": creds.refresh_token,
                }
                try:
                    update_worker(wid, {"google_refresh_token": creds.refresh_token})
                except Exception:
                    st.caption("※ Firestore へのトークン保存はスキップされました（未接続または権限）。")
                st.success(f"職人 {wid} の Google カレンダー連携が完了しました。")

            st.query_params.clear()
            st.page_link("pages/04_共通設定.py", label="共通設定へ戻る", icon="⚙️")
        elif creds and not creds.refresh_token:
            st.error(
                "アクセストークンは取得できましたが、リフレッシュトークンがありません。"
                "Google アカウントの「アプリへのアクセス」からこのアプリの接続を解除し、"
                "共通設定から連携をやり直してください（同意画面でオフラインアクセスが必要です）。"
            )
        else:
            st.error("認証コードの交換に失敗しました。もう一度お試しください。")
            if exch_err:
                st.code(exch_err, language="text")
            if exch_err and "scope" in exch_err.lower():
                st.caption(
                    "**スコープ（Scope）関連のエラー**のときは、Google アカウントの "
                    "[「アプリへのアクセス」](https://myaccount.google.com/permissions) で "
                    "このアプリの接続を解除してから、共通設定から連携をやり直してください。"
                    "（アプリは `calendar.events` のみを要求する設定です。）"
                )
            st.caption(
                "よくある原因: **リダイレクト URI の不一致**（ブラウザの URL と `.env` の "
                "`GOOGLE_OAUTH_REDIRECT_URI` が完全一致しているか）、"
                "**認証コードの期限切れ**（戻るボタンで同じ URL を開き直していないか）、"
                "**クライアントシークレットの誤り**、Cloud Console の OAuth クライアントに "
                "上記リダイレクト URI が登録されているか。"
            )
    else:
        st.info("このページは Google からのリダイレクト専用です。共通設定から連携を開始してください。")
        st.page_link("pages/04_共通設定.py", label="共通設定へ", icon="⚙️")


if __name__ == "__main__":
    render_page()
