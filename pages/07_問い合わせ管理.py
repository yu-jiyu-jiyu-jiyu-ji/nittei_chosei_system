"""問い合わせ管理（管理者：全件・ステータス・返信・開発ドラフト生成）."""

from __future__ import annotations

import os
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Any, Dict, List, Optional

import html

import streamlit as st

from config.constants import APP_TITLE, DB_UNAVAILABLE_MESSAGE
from services.firestore_service import FirestoreConnectionError, FirestoreSaveError
from services.inquiry_service import (
    append_admin_message,
    build_dev_prompt_draft,
    get_inquiry,
    list_all_inquiries,
    resolve_attachment_path,
    save_inquiry_attachment_files,
    update_inquiry_status,
)
from utils.layout_util import STREAMLIT_MENU_ITEMS, inject_sidebar_nav, inject_wide_layout
from utils.session_util import init_session_state

CATEGORY_LABEL = {"usage": "使い方", "system": "システム"}
STATUS_LABEL = {"open": "未対応", "in_progress": "対応中", "closed": "完了"}
STATUS_OPTIONS = ["open", "in_progress", "closed"]
ADMIN_PASS_ENV = "INQUIRY_ADMIN_PASSWORD"
_INQ_ADMIN_LOAD_TIMEOUT_SEC = 12.0


def _load_all_inquiries_with_timeout(timeout_sec: float = _INQ_ADMIN_LOAD_TIMEOUT_SEC) -> tuple[List[Dict[str, Any]], bool]:
    """問い合わせ一覧をタイムアウト付きで取得。失敗時は直近キャッシュを返す。"""
    cache_key = "_inquiry_admin_items_cache"
    with ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(list_all_inquiries)
        try:
            items = fut.result(timeout=timeout_sec)
            st.session_state[cache_key] = items
            return items, False
        except FuturesTimeoutError:
            cached = st.session_state.get(cache_key)
            if isinstance(cached, list):
                return cached, True
            raise TimeoutError("一覧取得がタイムアウトしました。")


def _render_attachment_images(paths: List[str]) -> None:
    """保存済み画像パスをグリッド表示."""
    if not paths:
        return
    cols = st.columns(min(4, len(paths)))
    for j, p in enumerate(paths):
        rp = resolve_attachment_path(str(p))
        with cols[j % len(cols)]:
            if rp:
                st.image(str(rp))
            else:
                st.caption(str(p))


def _format_ts(raw: Optional[str]) -> str:
    if not raw:
        return "—"
    try:
        s = str(raw).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt.strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return str(raw)


def _format_ts_date(raw: Optional[str]) -> str:
    if not raw:
        return "—"
    try:
        s = str(raw).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt.strftime("%Y/%m/%d")
    except (TypeError, ValueError):
        return str(raw)


_STATUS_BADGE_CLASS = {
    "open": "inq-status-open",
    "in_progress": "inq-status-progress",
    "closed": "inq-status-closed",
}


def _inject_inquiry_list_css() -> None:
    st.markdown(
        """
<style>
.inq-list { display: flex; flex-direction: column; gap: 0.85rem; }
.inq-card {
    border: 1px solid #e5e7eb;
    border-radius: 12px;
    padding: 1rem 1.1rem 0.35rem;
    background: #fff;
}
.inq-card.is-selected {
    border-color: #2563eb;
    box-shadow: 0 0 0 1px #2563eb;
    background: #f8fbff;
}
.inq-card-head {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 0.75rem;
    margin-bottom: 0.55rem;
}
.inq-card-meta {
    font-size: 0.82rem;
    color: #6b7280;
    line-height: 1.45;
}
.inq-status {
    display: inline-block;
    font-size: 0.74rem;
    font-weight: 700;
    padding: 0.22rem 0.6rem;
    border-radius: 999px;
    white-space: nowrap;
}
.inq-status-open { background: #fef3c7; color: #b45309; }
.inq-status-progress { background: #dbeafe; color: #1d4ed8; }
.inq-status-closed { background: #e5e7eb; color: #4b5563; }
.inq-card-title {
    font-size: 1.02rem;
    font-weight: 700;
    color: #111827;
    line-height: 1.5;
    margin: 0 0 0.15rem 0;
}
div[data-testid="stVerticalBlock"]:has(.inq-card) + div[data-testid="stVerticalBlock"] div[data-testid="stButton"] button {
    border: none;
    background: transparent;
    color: #2563eb;
    font-weight: 600;
    padding: 0.15rem 0 0.55rem;
    justify-content: flex-start;
    box-shadow: none;
}
div[data-testid="stVerticalBlock"]:has(.inq-card) + div[data-testid="stVerticalBlock"] div[data-testid="stButton"] button:hover {
    color: #1d4ed8;
    background: transparent;
}
</style>
        """,
        unsafe_allow_html=True,
    )


def _status_badge_html(status: str) -> str:
    key = status if status in _STATUS_BADGE_CLASS else "open"
    label = STATUS_LABEL.get(key, status)
    css = _STATUS_BADGE_CLASS.get(key, "inq-status-open")
    return f'<span class="inq-status {css}">{html.escape(label)}</span>'


def _build_inquiry_card_html(it: Dict[str, Any], *, selected: bool) -> str:
    status = str(it.get("status") or "open")
    category = html.escape(CATEGORY_LABEL.get(it.get("category"), "—"))
    email = html.escape(str(it.get("user_email") or "—"))
    title = html.escape(str(it.get("summary") or "（概要なし）"))
    date_s = html.escape(_format_ts_date(it.get("created_at")))
    sel_cls = " is-selected" if selected else ""
    return (
        f'<div class="inq-card{sel_cls}">'
        f'<div class="inq-card-head">'
        f'<div class="inq-card-meta">{category} · {email}<br>{date_s}</div>'
        f"{_status_badge_html(status)}"
        f"</div>"
        f'<p class="inq-card-title">{title}</p>'
        f"</div>"
    )


def _resolve_selected_index(items: List[Dict[str, Any]]) -> int:
    """セッション保存 ID から表示対象の index を決める."""
    selected_id = str(st.session_state.get("inq_admin_selected_id") or "").strip()
    if selected_id:
        for i, it in enumerate(items):
            if str(it.get("inquiry_id") or "") == selected_id:
                return i
    st.session_state["inq_admin_selected_id"] = str(items[0].get("inquiry_id") or "")
    return 0


def _render_inquiry_card_list(items: List[Dict[str, Any]], *, selected_id: str) -> None:
    """カード型の問い合わせ一覧を描画する."""
    _inject_inquiry_list_css()
    st.markdown('<div class="inq-list">', unsafe_allow_html=True)
    for it in items:
        inquiry_id = str(it.get("inquiry_id") or "")
        selected = inquiry_id == selected_id
        st.markdown(_build_inquiry_card_html(it, selected=selected), unsafe_allow_html=True)
        if st.button("詳細を見る →", key=f"inq_pick_{inquiry_id}", type="tertiary"):
            st.session_state["inq_admin_selected_id"] = inquiry_id
            st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)


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
    timed_out = False
    try:
        with st.spinner("問い合わせ一覧を読み込み中です…"):
            items, timed_out = _load_all_inquiries_with_timeout()
    except FirestoreConnectionError:
        st.error(DB_UNAVAILABLE_MESSAGE)
        st.stop()
    except TimeoutError:
        st.error("読み込みが長引いています。通信状態を確認して再読み込みしてください。")
        st.stop()
    except Exception as e:
        st.error(f"一覧の取得に失敗しました: {e}")
        st.stop()

    if not items:
        st.info("問い合わせはまだありません。")
        st.stop()
    if timed_out:
        st.warning("最新データの取得がタイムアウトしたため、直近の表示データを表示しています。")

    admin_name = str(st.session_state.get("current_user_name") or "").strip()
    ix = _resolve_selected_index(items)
    row = items[ix]
    inquiry_id = str(row.get("inquiry_id") or "")
    selected_id = inquiry_id

    list_col, detail_col = st.columns([2, 3])
    with list_col:
        st.markdown("##### 全件一覧")
        st.caption("カードの「詳細を見る」から内容を表示できます。")
        _render_inquiry_card_list(items, selected_id=selected_id)

    with detail_col:
        st.markdown("##### 詳細・操作")
        st.write(
            f"**{row.get('summary', '')}** ／ {CATEGORY_LABEL.get(row.get('category'), '')} ／ "
            f"{row.get('user_name', '')} `<{row.get('user_email', '')}>`"
        )
        st.caption(
            f"作成: {_format_ts(row.get('created_at'))} ／ "
            f"ステータス: {STATUS_LABEL.get(row.get('status') or 'open', '—')}"
        )

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
        reply_uploads = st.file_uploader(
            "返信に画像を添付（複数可・任意）",
            type=["png", "jpg", "jpeg", "gif", "webp"],
            accept_multiple_files=True,
            key=f"inq_reply_files_{inquiry_id}",
        )
        c1, c2, c3 = st.columns([1, 1, 2])
        with c1:
            if st.button("返信を送信", type="primary", key=f"inq_reply_send_{inquiry_id}"):
                try:
                    image_paths: List[str] = []
                    if reply_uploads:
                        file_list = [(uf.name, uf.getvalue()) for uf in reply_uploads]
                        image_paths = save_inquiry_attachment_files(inquiry_id, file_list)
                    append_admin_message(
                        inquiry_id,
                        reply,
                        admin_name=admin_name or None,
                        image_paths=image_paths or None,
                    )
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
                    if m.get("content"):
                        st.write(m.get("content", ""))
                    msg_images = m.get("image_urls") or []
                    if msg_images:
                        _render_attachment_images([str(p) for p in msg_images])


def main() -> None:
    render_page()


if __name__ == "__main__":
    main()
