"""問い合わせ一覧のカード型UI（履歴・管理で共通）."""

from __future__ import annotations

import html
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

import streamlit as st

CATEGORY_LABEL = {"usage": "使い方", "system": "システム"}
STATUS_LABEL = {"open": "未対応", "in_progress": "対応中", "closed": "完了"}

_STATUS_BADGE_CLASS = {
    "open": "inq-status-open",
    "in_progress": "inq-status-progress",
    "closed": "inq-status-closed",
}


def format_inquiry_ts(raw: Optional[str]) -> str:
    if not raw:
        return "—"
    try:
        s = str(raw).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt.strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return str(raw)


def format_inquiry_ts_date(raw: Optional[str]) -> str:
    if not raw:
        return "—"
    try:
        s = str(raw).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt.strftime("%Y/%m/%d")
    except (TypeError, ValueError):
        return str(raw)


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


def build_inquiry_card_html(
    it: Dict[str, Any],
    *,
    selected: bool,
    meta_line: Optional[str] = None,
) -> str:
    """カード1件分の HTML を組み立てる."""
    status = str(it.get("status") or "open")
    date_s = html.escape(format_inquiry_ts_date(it.get("created_at")))
    if meta_line is None:
        category = html.escape(CATEGORY_LABEL.get(it.get("category"), "—"))
        email = html.escape(str(it.get("user_email") or "—"))
        meta_html = f"{category} · {email}<br>{date_s}"
    else:
        meta_html = f"{html.escape(meta_line)}<br>{date_s}"

    title = html.escape(str(it.get("summary") or "（概要なし）"))
    sel_cls = " is-selected" if selected else ""
    return (
        f'<div class="inq-card{sel_cls}">'
        f'<div class="inq-card-head">'
        f'<div class="inq-card-meta">{meta_html}</div>'
        f"{_status_badge_html(status)}"
        f"</div>"
        f'<p class="inq-card-title">{title}</p>'
        f"</div>"
    )


def resolve_inquiry_selected_index(
    items: List[Dict[str, Any]],
    *,
    session_key: str,
) -> int:
    """セッション保存 ID から表示対象の index を決める."""
    selected_id = str(st.session_state.get(session_key) or "").strip()
    if selected_id:
        for i, it in enumerate(items):
            if str(it.get("inquiry_id") or "") == selected_id:
                return i
    st.session_state[session_key] = str(items[0].get("inquiry_id") or "")
    return 0


def render_inquiry_card_list(
    items: List[Dict[str, Any]],
    *,
    selected_id: str,
    session_key: str,
    button_key_prefix: str,
    meta_line_builder: Optional[Callable[[Dict[str, Any]], str]] = None,
) -> None:
    """カード型の問い合わせ一覧を描画する."""
    _inject_inquiry_list_css()
    st.markdown('<div class="inq-list">', unsafe_allow_html=True)
    for it in items:
        inquiry_id = str(it.get("inquiry_id") or "")
        selected = inquiry_id == selected_id
        meta_line = meta_line_builder(it) if meta_line_builder else None
        st.markdown(
            build_inquiry_card_html(it, selected=selected, meta_line=meta_line),
            unsafe_allow_html=True,
        )
        if st.button("詳細を見る →", key=f"{button_key_prefix}{inquiry_id}", type="tertiary"):
            st.session_state[session_key] = inquiry_id
            st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)
