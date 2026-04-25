"""問い合わせ管理（管理者：全件・ステータス・返信・開発ドラフト生成）."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import streamlit as st

from config.constants import APP_TITLE, DB_UNAVAILABLE_MESSAGE
from services.firestore_service import FirestoreConnectionError, FirestoreSaveError
from services.inquiry_service import (
    append_admin_message,
    build_dev_prompt_draft,
    get_inquiry,
    list_all_inquiries,
    resolve_attachment_path,
    update_inquiry_status,
)
from utils.layout_util import STREAMLIT_MENU_ITEMS, inject_sidebar_nav, inject_wide_layout
from utils.session_util import init_session_state

CATEGORY_LABEL = {"usage": "使い方", "system": "システム"}
STATUS_LABEL = {"open": "未対応", "in_progress": "対応中", "closed": "完了"}
STATUS_OPTIONS = ["open", "in_progress", "closed"]
ADMIN_PASS_ENV = "INQUIRY_ADMIN_PASSWORD"


def _format_ts(raw: Optional[str]) -> str:
    if not raw:
        return "—"
    try:
        s = str(raw).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt.strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return str(raw)


@st.dialog("開発用プロンプト（ドラフト）")
def _open_dev_draft_dialog(body: str, *, dialog_key: str) -> None:
    st.caption("テンプレートから生成したドラフトです。選択してコピーするか、ダウンロードしてください。")
    st.text_area("内容", value=body, height=360, key=f"inq_admin_dev_body_{dialog_key}")
    st.download_button(
        "テキストファイルでダウンロード",
        data=body.encode("utf-8"),
        file_name="inquiry-dev-prompt.txt",
        mime="text/plain; charset=utf-8",
        key=f"inq_admin_dev_dl_{dialog_key}",
    )


@st.dialog("問い合わせ管理の閲覧認証")
def _open_admin_auth_dialog() -> None:
    st.caption("この画面を開くにはパスワードの入力が必要です。")
    entered = st.text_input("パスワード", type="password", key="inq_admin_pw_input")
    if st.button("認証する", type="primary", key="inq_admin_pw_submit"):
        expected = str(os.environ.get(ADMIN_PASS_ENV) or "").strip()
        if not expected:
            st.error(f"サーバ設定に {ADMIN_PASS_ENV} がありません。管理者に確認してください。")
            return
        if entered == expected:
            st.session_state["inquiry_admin_unlocked"] = True
            st.session_state.pop("inq_admin_pw_input", None)
            st.success("認証に成功しました。")
            st.rerun()
        st.error("パスワードが違います。")


def render_page() -> None:
    st.set_page_config(
        page_title=f"{APP_TITLE} - 問い合わせ管理",
        layout="wide",
        menu_items=STREAMLIT_MENU_ITEMS,
    )
    init_session_state()
    st.session_state["_active_page_id"] = "inquiries_admin"
    inject_wide_layout()
    inject_sidebar_nav()

    if st.session_state.get("current_user_role") != "admin":
        st.error("このページは管理者のみ利用できます。")
        st.stop()
    if not st.session_state.get("inquiry_admin_unlocked", False):
        _open_admin_auth_dialog()
        st.info("認証後に問い合わせ管理の内容を表示します。")
        st.stop()

    st.title("問い合わせ管理")
    st.caption("すべての問い合わせを確認し、ステータス更新・返信・開発用ドラフトの生成ができます。")

    items: List[Dict[str, Any]] = []
    try:
        items = list_all_inquiries()
    except FirestoreConnectionError:
        st.error(DB_UNAVAILABLE_MESSAGE)
        st.stop()
    except Exception as e:
        st.error(f"一覧の取得に失敗しました: {e}")
        st.stop()

    if not items:
        st.info("問い合わせはまだありません。")
        st.stop()

    admin_name = str(st.session_state.get("current_user_name") or "").strip()

    left, right = st.columns([1, 2])
    with left:
        st.markdown("##### 全件一覧")
        options = list(range(len(items)))
        labels = [
            f"{_format_ts(it.get('created_at'))} · {it.get('user_email', '')}\n"
            f"{(it.get('summary') or '')[:72]}"
            for it in items
        ]
        ix = st.radio(
            "選択",
            options,
            format_func=lambda i: labels[i],
            key="inq_admin_idx",
            label_visibility="collapsed",
        )

    row = items[ix]
    inquiry_id = str(row.get("inquiry_id") or "")

    with right:
        st.markdown("##### 詳細・操作")
        st.write(
            f"**{row.get('summary', '')}** ／ {CATEGORY_LABEL.get(row.get('category'), '')} ／ "
            f"{row.get('user_name', '')} `<{row.get('user_email', '')}>`"
        )
        st.caption(f"作成: {_format_ts(row.get('created_at'))}")

        cur_status = row.get("status") or "open"
        new_status = st.selectbox(
            "ステータス",
            options=STATUS_OPTIONS,
            index=STATUS_OPTIONS.index(cur_status) if cur_status in STATUS_OPTIONS else 0,
            format_func=lambda s: STATUS_LABEL.get(s, s),
            key=f"inq_status_{inquiry_id}",
        )
        if st.button("ステータスを保存", key=f"inq_status_save_{inquiry_id}"):
            try:
                update_inquiry_status(inquiry_id, new_status)
                st.success("更新しました。")
                st.rerun()
            except (FirestoreConnectionError, FirestoreSaveError) as e:
                st.error(str(e))

        st.divider()
        st.markdown("**内容**")
        st.text(row.get("detail") or "（なし）")

        paths = row.get("image_urls") or []
        if paths:
            st.markdown("**添付画像**")
            cols = st.columns(min(4, len(paths)))
            for j, p in enumerate(paths):
                rp = resolve_attachment_path(str(p))
                with cols[j % len(cols)]:
                    if rp:
                        st.image(str(rp))
                    else:
                        st.caption(str(p))

        st.divider()
        reply = st.text_area("返信を入力（管理者）", key=f"inq_reply_{inquiry_id}", height=120)
        c1, c2, c3 = st.columns([1, 1, 2])
        with c1:
            if st.button("返信を送信", type="primary", key=f"inq_reply_send_{inquiry_id}"):
                try:
                    append_admin_message(inquiry_id, reply, admin_name=admin_name or None)
                    st.success("返信を記録しました。")
                    st.rerun()
                except (FirestoreConnectionError, FirestoreSaveError) as e:
                    st.error(str(e))
                except ValueError as e:
                    st.error(str(e))
        with c2:
            if st.button("開発ドラフト生成", key=f"inq_dev_{inquiry_id}"):
                fresh = get_inquiry(inquiry_id) or row
                _open_dev_draft_dialog(build_dev_prompt_draft(fresh), dialog_key=inquiry_id)
        with c3:
            pass

        st.markdown("**messages**")
        msgs = row.get("messages") or []
        if not msgs:
            st.caption("（なし）")
        else:
            for m in msgs:
                with st.chat_message("assistant" if m.get("role") == "admin" else "user"):
                    who = "管理者" if m.get("role") == "admin" else (m.get("sender_name") or "起票者")
                    st.caption(f"{who} · {_format_ts(m.get('created_at'))}")
                    st.write(m.get("content", ""))


def main() -> None:
    render_page()


if __name__ == "__main__":
    main()
