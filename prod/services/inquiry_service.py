"""問い合わせ（inquiries）の読み書き."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from services.firestore_service import (
    FirestoreConnectionError,
    FirestoreSaveError,
    doc_to_dict,
    require_firestore_client,
)

_COLLECTION = "inquiries"
_ROOT = Path(__file__).resolve().parent.parent
_UPLOAD_ROOT = _ROOT / "data" / "inquiry_files"


def _inquiry_doc_to_dict(doc: Any) -> Dict[str, Any]:
    data = doc_to_dict(doc)
    data["inquiry_id"] = doc.id
    return data


def _safe_upload_name(original: str) -> str:
    base = Path(original).name
    if ".." in base or "/" in base or "\\" in base:
        return f"{uuid4().hex}.bin"
    return base[:240] if base else f"{uuid4().hex}.bin"


def save_inquiry_attachment_files(inquiry_id: str, file_bytes_list: List[tuple[str, bytes]]) -> List[str]:
    """アップロードバイトを data/inquiry_files/{inquiry_id}/ に保存し、プロジェクトルートからの相対パスを返す."""
    paths: List[str] = []
    target = _UPLOAD_ROOT / inquiry_id
    target.mkdir(parents=True, exist_ok=True)
    for original_name, raw in file_bytes_list:
        name = _safe_upload_name(original_name)
        unique = f"{uuid4().hex[:8]}_{name}"
        dest = target / unique
        dest.write_bytes(raw)
        rel = dest.relative_to(_ROOT)
        paths.append(rel.as_posix())
    return paths


def resolve_attachment_path(stored: str) -> Optional[Path]:
    """保存パス文字列をローカル Path に解決."""
    if not stored or ".." in stored:
        return None
    p = (_ROOT / stored).resolve()
    try:
        p.relative_to(_ROOT.resolve())
    except ValueError:
        return None
    return p if p.is_file() else None


def create_inquiry(
    *,
    category: str,
    summary: str,
    detail: str,
    user_email: str,
    user_name: str,
    image_paths: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """問い合わせを新規作成."""
    if category not in ("system", "usage"):
        raise ValueError("category は system または usage です。")
    summary = (summary or "").strip()
    if not summary:
        raise ValueError("概要は必須です。")
    client = require_firestore_client()
    try:
        now = datetime.utcnow()
        inquiry_id = uuid4().hex
        doc = {
            "category": category,
            "summary": summary,
            "detail": detail or "",
            "status": "open",
            "user_email": (user_email or "").strip(),
            "user_name": (user_name or "").strip(),
            "image_urls": image_paths or [],
            "messages": [],
            "created_at": now,
            "updated_at": now,
        }
        client.collection(_COLLECTION).document(inquiry_id).set(doc)
        out = {**doc, "inquiry_id": inquiry_id}
        out["created_at"] = now.isoformat()
        out["updated_at"] = now.isoformat()
        return out
    except FirestoreConnectionError:
        raise
    except Exception as e:
        raise FirestoreSaveError(f"問い合わせの保存に失敗しました: {e}") from e


def update_inquiry_image_paths(inquiry_id: str, image_paths: List[str]) -> None:
    """添付ファイル保存後に image_urls を更新。"""
    client = require_firestore_client()
    ref = client.collection(_COLLECTION).document(inquiry_id)
    doc = ref.get()
    if not doc.exists:
        raise FirestoreSaveError("問い合わせが見つかりません。")
    data = doc.to_dict() or {}
    data["image_urls"] = image_paths
    data["updated_at"] = datetime.utcnow()
    ref.set(data)


def get_inquiry(inquiry_id: str) -> Optional[Dict[str, Any]]:
    client = require_firestore_client()
    ref = client.collection(_COLLECTION).document(inquiry_id).get()
    if not ref.exists:
        return None
    return _inquiry_doc_to_dict(ref)


def list_inquiries_for_user(user_email: str) -> List[Dict[str, Any]]:
    """指定メールの問い合わせ一覧（新しい順）."""
    email = (user_email or "").strip()
    if not email:
        return []
    client = require_firestore_client()
    rows: List[Dict[str, Any]] = []
    for doc in client.collection(_COLLECTION).where("user_email", "==", email).stream():
        rows.append(_inquiry_doc_to_dict(doc))

    def _sort_key(r: Dict[str, Any]) -> str:
        return str(r.get("created_at") or "")

    rows.sort(key=_sort_key, reverse=True)
    return rows


def list_all_inquiries() -> List[Dict[str, Any]]:
    """全問い合わせ（管理者向け）。"""
    client = require_firestore_client()
    rows = [_inquiry_doc_to_dict(doc) for doc in client.collection(_COLLECTION).stream()]

    def _sort_key(r: Dict[str, Any]) -> str:
        return str(r.get("created_at") or "")

    rows.sort(key=_sort_key, reverse=True)
    return rows


def update_inquiry_status(inquiry_id: str, status: str) -> Optional[Dict[str, Any]]:
    if status not in ("open", "in_progress", "closed"):
        raise ValueError("status が不正です。")
    client = require_firestore_client()
    ref = client.collection(_COLLECTION).document(inquiry_id)
    doc = ref.get()
    if not doc.exists:
        return None
    now = datetime.utcnow()
    data = doc.to_dict() or {}
    data["status"] = status
    data["updated_at"] = now
    ref.set(data)
    return get_inquiry(inquiry_id)


def append_inquirer_message(
    inquiry_id: str,
    content: str,
    *,
    user_email: str,
    user_name: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """起票者（user_email が一致するユーザー）の返信を messages に追記。"""
    text = (content or "").strip()
    if not text:
        raise ValueError("返信内容を入力してください。")
    email = (user_email or "").strip()
    if not email:
        raise ValueError("ユーザーメールが未設定です。")
    client = require_firestore_client()
    ref = client.collection(_COLLECTION).document(inquiry_id)
    doc = ref.get()
    if not doc.exists:
        return None
    data = doc.to_dict() or {}
    owner = (data.get("user_email") or "").strip()
    if owner != email:
        raise FirestoreSaveError("この問い合わせに返信する権限がありません。")
    messages = list(data.get("messages") or [])
    created_at = datetime.utcnow().isoformat() + "Z"
    entry: Dict[str, Any] = {
        "role": "user",
        "content": text,
        "created_at": created_at,
    }
    if user_name:
        entry["sender_name"] = user_name
    messages.append(entry)
    data["messages"] = messages
    data["updated_at"] = datetime.utcnow()
    ref.set(data)
    return get_inquiry(inquiry_id)


def append_admin_message(inquiry_id: str, content: str, *, admin_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """管理者返信を messages に追記。"""
    text = (content or "").strip()
    if not text:
        raise ValueError("返信内容を入力してください。")
    client = require_firestore_client()
    ref = client.collection(_COLLECTION).document(inquiry_id)
    doc = ref.get()
    if not doc.exists:
        return None
    data = doc.to_dict() or {}
    messages = list(data.get("messages") or [])
    created_at = datetime.utcnow().isoformat() + "Z"
    entry: Dict[str, Any] = {
        "role": "admin",
        "content": text,
        "created_at": created_at,
    }
    if admin_name:
        entry["sender_name"] = admin_name
    messages.append(entry)
    data["messages"] = messages
    data["updated_at"] = datetime.utcnow()
    ref.set(data)
    return get_inquiry(inquiry_id)


def build_dev_prompt_draft(inquiry: Dict[str, Any]) -> str:
    """開発向けプロンプトのテンプレート生成（外部 LLM 不要）。"""
    lines = [
        "# 開発用プロンプト（ドラフト）",
        "",
        "## 背景・概要",
        str(inquiry.get("summary") or ""),
        "",
        "## 詳細・再現手順・期待動作",
        str(inquiry.get("detail") or ""),
        "",
        f"## メタデータ（種別: {inquiry.get('category')} / ステータス: {inquiry.get('status')}）",
        f"- 送信者: {inquiry.get('user_name')} <{inquiry.get('user_email')}>",
        "",
        "## 依頼（このブロックを開発タスクに貼り付け）",
        "- [ ] 現象を再現し、原因を特定する",
        "- [ ] 仕様として正しい動作を定義する",
        "- [ ] 修正方針と影響範囲（データ・他画面）を記載する",
        "- [ ] テスト観点（正常系・境界・回帰）を列挙する",
        "",
    ]
    msgs = inquiry.get("messages") or []
    if msgs:
        lines.append("## 既存のやりとり（要約用）")
        for m in msgs:
            role = m.get("role", "")
            lines.append(f"- [{role}] {m.get('content', '')[:500]}")
        lines.append("")
    return "\n".join(lines)
