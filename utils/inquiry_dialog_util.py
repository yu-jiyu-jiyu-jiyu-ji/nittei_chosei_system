"""問い合わせの新規投稿（st.dialog）。案件詳細ダイアログと同様にポップアップで入力する."""

from __future__ import annotations

from typing import List

import streamlit as st

from config.constants import DB_UNAVAILABLE_MESSAGE
from services.firestore_service import FirestoreConnectionError, FirestoreSaveError
from services.inquiry_service import (
    create_inquiry,
    save_inquiry_attachment_files,
    update_inquiry_image_paths,
)

# 種別は日本語ラベルのみ（自由入力・英語キー非表示）
INQUIRY_CATEGORY_DROPDOWN_LABELS = ("使い方に関する問合せ", "システムに関する問合せ")
INQUIRY_CATEGORY_LABEL_TO_KEY = {
    "使い方に関する問合せ": "usage",
    "システムに関する問合せ": "system",
}


def build_inquiry_detail(category: str, usage_detail: str, sys_cur: str, sys_exp: str) -> str:
    """種別に応じた detail 文字列を組み立てる."""
    if category == "usage":
        return (usage_detail or "").strip()
    return "\n".join(
        [
            "【現在の状態】",
            (sys_cur or "").strip(),
            "",
            "【修正後の状態】",
            (sys_exp or "").strip(),
        ]
    )


def _ensure_float_init() -> None:
    """streamlit-float の CSS をセッションで1回だけ注入."""
    if st.session_state.get("_inquiry_float_init_done"):
        return
    from streamlit_float import float_init

    float_init(theme=True)
    st.session_state["_inquiry_float_init_done"] = True


@st.dialog("問い合わせ")
def open_inquiry_dialog() -> None:
    """フローティング「?」から開く新規問い合わせモーダル."""
    st.caption("種別・概要・詳細を入力して送信してください。画像は任意です。")
    user_email = str(st.session_state.get("current_user_email") or "").strip()
    user_name = str(st.session_state.get("current_user_name") or "").strip()

    if not user_email:
        st.warning(
            "送信にはメールアドレスが必要です。`current_user_email` をセッション（または共通設定）で設定してください。"
        )

    cat_label = st.selectbox(
        "種別",
        options=list(INQUIRY_CATEGORY_DROPDOWN_LABELS),
        index=0,
        key="inq_fab_category",
        help="一覧から選択してください（キーボードで新規作成はできません）。",
    )
    cat = INQUIRY_CATEGORY_LABEL_TO_KEY[cat_label]
    summary = st.text_input("概要（必須）", key="inq_fab_summary")

    usage_detail = ""
    sys_cur = ""
    sys_exp = ""
    if cat == "usage":
        usage_detail = st.text_area("詳細", key="inq_fab_usage_detail", height=160)
    else:
        sys_cur = st.text_area("今の状態", key="inq_fab_sys_cur", height=120)
        sys_exp = st.text_area("修正後の状態", key="inq_fab_sys_exp", height=120)

    uploads = st.file_uploader(
        "画像添付（複数可）",
        type=["png", "jpg", "jpeg", "gif", "webp"],
        accept_multiple_files=True,
        key="inq_fab_files",
    )

    c1, c2 = st.columns(2)
    with c1:
        if st.button("送信する", type="primary", key="inq_fab_submit", disabled=not user_email):
            if not (summary or "").strip():
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
                except FirestoreConnectionError:
                    st.error(DB_UNAVAILABLE_MESSAGE)
                except FirestoreSaveError as e:
                    st.error(str(e))
                except ValueError as e:
                    st.error(str(e))
    with c2:
        st.page_link("pages/06_問い合わせ履歴.py", label="履歴ページを開く", icon="💬")


def render_inquiry_floating_button() -> None:
    """右下固定の「?」ボタン。押下で open_inquiry_dialog を表示."""
    _ensure_float_init()

    fab = st.container()
    with fab:
        if st.button(
            "?",
            type="primary",
            key="inquiry_floating_fab",
            help="問い合わせ（ポップアップ）",
        ):
            open_inquiry_dialog()
    # 右下に固定（streamlit-float が左寄りになる場合は left を明示的に解除）
    fab.float(
        css=(
            "position: fixed !important; "
            "inset: auto 1.25rem 1.25rem auto !important; "
            "left: auto !important; "
            "right: 1.25rem !important; "
            "bottom: 1.25rem !important; "
            "top: auto !important; "
            "width: auto !important; "
            "max-width: 4.5rem !important; "
            "z-index: 100 !important;"
        )
    )
