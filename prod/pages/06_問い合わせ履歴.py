"""問い合わせ履歴（自分の投稿の一覧・詳細・新規投稿）."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

import streamlit as st

from config.constants import APP_TITLE, DB_UNAVAILABLE_MESSAGE
from services.firestore_service import FirestoreConnectionError, FirestoreSaveError
from services.inquiry_service import (
    append_inquirer_message,
    create_inquiry,
    list_inquiries_for_user,
    resolve_attachment_path,
    save_inquiry_attachment_files,
    update_inquiry_image_paths,
)
from utils.inquiry_dialog_util import (
    INQUIRY_CATEGORY_LABEL_TO_KEY,
    INQUIRY_CATEGORY_DROPDOWN_LABELS,
    build_inquiry_detail,
)
from utils.layout_util import STREAMLIT_MENU_ITEMS, inject_sidebar_nav, inject_wide_layout
from utils.session_util import init_session_state

CATEGORY_LABEL = {"usage": "使い方", "system": "システム"}
STATUS_LABEL = {"open": "未対応", "in_progress": "対応中", "closed": "完了"}


def _format_ts(raw: Optional[str]) -> str:
    if not raw:
        return "—"
    try:
        s = str(raw).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt.strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return str(raw)


def _admin_reply_count(row: Dict[str, Any]) -> int:
    return len([m for m in (row.get("messages") or []) if m.get("role") == "admin"])


def render_page() -> None:
    st.set_page_config(
        page_title=f"{APP_TITLE} - 問い合わせ履歴",
        layout="wide",
        menu_items=STREAMLIT_MENU_ITEMS,
    )
    init_session_state()
    st.session_state["_active_page_id"] = "inquiries_mine"
    inject_wide_layout()
    inject_sidebar_nav()

    user_email = str(st.session_state.get("current_user_email") or "").strip()
    user_name = str(st.session_state.get("current_user_name") or "").strip()
    reply_user_name = "ユーザー" if user_name == "開発ユーザー" else user_name

    st.title("問い合わせ履歴")
    st.caption("ご自身の問い合わせの投稿・状況・管理者からの返信を確認できます。")

    items: List[Dict[str, Any]] = []
    try:
        items = list_inquiries_for_user(user_email) if user_email else []
    except FirestoreConnectionError:
        st.error(DB_UNAVAILABLE_MESSAGE)
        st.stop()
    except Exception as e:
        st.error(f"一覧の取得に失敗しました: {e}")
        st.stop()

    with st.expander("新規問い合わせ", expanded=False):
        cat_label = st.selectbox(
            "種別",
            options=list(INQUIRY_CATEGORY_DROPDOWN_LABELS),
            index=0,
            key="inq_new_category",
            help="一覧から選択してください。",
        )
        cat = INQUIRY_CATEGORY_LABEL_TO_KEY[cat_label]
        summary = st.text_input("概要（必須）", key="inq_new_summary")
        usage_detail = ""
        sys_cur = ""
        sys_exp = ""
        if cat == "usage":
            usage_detail = st.text_area("詳細", key="inq_new_usage_detail", height=160)
        else:
            sys_cur = st.text_area("今の状態", key="inq_new_sys_cur", height=120)
            sys_exp = st.text_area("修正後の状態", key="inq_new_sys_exp", height=120)
        uploads = st.file_uploader(
            "画像添付（複数可）",
            type=["png", "jpg", "jpeg", "gif", "webp"],
            accept_multiple_files=True,
            key="inq_new_files",
        )
        if st.button("送信する", type="primary", key="inq_new_submit"):
            if not user_email:
                st.error("ユーザーメールが未設定です。セッション current_user_email を設定してください。")
            elif not (summary or "").strip():
                st.error("概要を入力してください。")
            elif cat == "usage" and not (usage_detail or "").strip():
                st.error("詳細を入力してください。")
            elif cat == "system" and (
                not (sys_cur or "").strip() or not (sys_exp or "").strip()
            ):
                st.error("「今の状態」と「修正後の状態」の両方を入力してください。")
            else:
                detail = build_inquiry_detail(cat, usage_detail, sys_cur, sys_exp)
                file_list: List[tuple[str, bytes]] = []
                if uploads:
                    for uf in uploads:
                        file_list.append((uf.name, uf.getvalue()))
                try:
                    created = create_inquiry(
                        category=cat,
                        summary=summary.strip(),
                        detail=detail,
                        user_email=user_email,
                        user_name=user_name or user_email,
                        image_paths=[],
                    )
                    iid = created.get("inquiry_id")
                    if iid and file_list:
                        paths = save_inquiry_attachment_files(iid, file_list)
                        update_inquiry_image_paths(iid, paths)
                    st.success("送信しました。")
                    st.rerun()
                except (FirestoreConnectionError, FirestoreSaveError) as e:
                    st.error(str(e))
                except ValueError as e:
                    st.error(str(e))

    if not user_email:
        st.warning("問い合わせにはメールアドレスが必要です。`current_user_email` をセッションに設定してください。")
        st.stop()

    if not items:
        st.info("まだ問い合わせはありません。")
        st.stop()

    left, right = st.columns([1, 2])
    with left:
        st.markdown("##### 一覧")
        options = list(range(len(items)))
        labels = [
            f"[{CATEGORY_LABEL.get(it.get('category'), '')}] {STATUS_LABEL.get(it.get('status'), '')} "
            f"· 返信{_admin_reply_count(it)} · {_format_ts(it.get('created_at'))} — "
            f"{(it.get('summary') or '')[:56]}"
            for it in items
        ]
        ix = st.radio(
            "件名",
            options,
            format_func=lambda i: labels[i],
            key="inq_selected_idx",
            label_visibility="collapsed",
        )

    row = items[ix]
    with right:
        st.markdown("##### 詳細")
        c1, c2 = st.columns(2)
        with c1:
            st.write("種別:", CATEGORY_LABEL.get(row.get("category"), "—"))
        with c2:
            st.write("ステータス:", STATUS_LABEL.get(row.get("status"), "—"))
        st.write("日時:", _format_ts(row.get("created_at")))
        st.markdown("**概要**")
        st.write(row.get("summary") or "—")
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
                        st.caption(p)

        st.markdown("**やりとり**")
        msgs = row.get("messages") or []
        if not msgs:
            st.info("まだメッセージはありません。")
        else:
            for m in msgs:
                role = m.get("role", "")
                is_adm = role == "admin"
                with st.chat_message("assistant" if is_adm else "user"):
                    if is_adm:
                        who = "管理者"
                    else:
                        who = (m.get("sender_name") or "").strip() or "起票者"
                    st.caption(f"{who} · {_format_ts(m.get('created_at'))}")
                    st.write(m.get("content", ""))

        st.divider()
        st.markdown("**返信を追加（起票者）**")
        iid = str(row.get("inquiry_id") or "")
        ureply = st.text_area(
            "内容",
            key=f"inq_user_reply_{iid}",
            height=100,
            placeholder="追記・回答・補足を入力して送信できます。",
        )
        if st.button("返信を送信", type="primary", key=f"inq_user_reply_send_{iid}"):
            try:
                append_inquirer_message(
                    iid,
                    ureply,
                    user_email=user_email,
                    user_name=reply_user_name or None,
                )
                st.success("返信を記録しました。")
                st.rerun()
            except (FirestoreConnectionError, FirestoreSaveError) as e:
                st.error(str(e))
            except ValueError as e:
                st.error(str(e))


def main() -> None:
    render_page()


if __name__ == "__main__":
    main()
