"""職人（workers）の読み書きサービス.

Firestore を優先し、接続不可時はダミーストアにフォールバックする。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import streamlit as st

from services.firestore_service import (
    FirestoreConnectionError,
    FirestoreSaveError,
    doc_to_dict,
    try_get_firestore_client,
)


def _get_dummy_workers() -> List[Dict[str, Any]]:
    """職人のダミーデータ（フォールバック用）."""
    if "dummy_workers" not in st.session_state:
        st.session_state["dummy_workers"] = [
            {
                "worker_id": "W001",
                "name": "山田太郎",
                "email": "",
                "calendar_id": "worker1@example.com",
                "is_active": True,
                "role": "",
                "note": "",
                "display_order": 1,
            },
            {
                "worker_id": "W002",
                "name": "佐藤花子",
                "email": "",
                "calendar_id": "worker2@example.com",
                "is_active": True,
                "role": "",
                "note": "",
                "display_order": 2,
            },
            {
                "worker_id": "W003",
                "name": "鈴木一郎",
                "email": "",
                "calendar_id": "worker3@example.com",
                "is_active": True,
                "role": "",
                "note": "",
                "display_order": 3,
            },
        ]
    return st.session_state["dummy_workers"]


def _generate_worker_id_from_store(store: List[Dict[str, Any]]) -> str:
    """ストアから職人ID採番."""
    next_num = max((int(w.get("worker_id", "W000")[1:]) for w in store), default=0) + 1
    return f"W{next_num:03d}"


def _generate_worker_id_firestore(client: Any) -> str:
    """Firestore から職人ID採番."""
    coll = client.collection("workers")
    docs = list(coll.stream())
    if not docs:
        return "W001"
    numbers = []
    for d in docs:
        data = d.to_dict()
        wid = data.get("worker_id", "") or d.id
        if isinstance(wid, str) and wid.startswith("W") and len(wid) >= 2:
            try:
                numbers.append(int(wid[1:]))
            except ValueError:
                pass
    next_num = max(numbers, default=0) + 1
    return f"W{next_num:03d}"


def list_workers() -> List[Dict[str, Any]]:
    """職人一覧を取得."""
    client = try_get_firestore_client()
    if client:
        try:
            coll = client.collection("workers")
            docs = list(coll.stream())
            workers = []
            for d in docs:
                data = doc_to_dict(d)
                data["worker_id"] = data.get("worker_id") or d.id
                workers.append(data)
        except FirestoreConnectionError:
            raise
        except Exception as e:
            raise FirestoreConnectionError(f"職人一覧の取得に失敗しました: {e}") from e
    else:
        workers = list(_get_dummy_workers())

    workers.sort(key=lambda x: (x.get("display_order", 999), x.get("worker_id", "")))
    return workers


def create_worker(data: Dict[str, Any]) -> Dict[str, Any]:
    """職人を新規作成."""
    client = try_get_firestore_client()
    if client:
        try:
            worker_id = _generate_worker_id_firestore(client)
            worker = {
                "worker_id": worker_id,
                "name": str(data.get("name", "")).strip(),
                "email": str(data.get("email", "")).strip(),
                "calendar_id": str(data.get("calendar_id", "")).strip(),
                "is_active": bool(data.get("is_active", True)),
                "role": str(data.get("role", "")).strip(),
                "note": str(data.get("note", "")).strip(),
                "display_order": int(data.get("display_order", 0)),
            }
            client.collection("workers").document(worker_id).set(worker)
            return worker
        except FirestoreConnectionError:
            raise
        except Exception as e:
            raise FirestoreSaveError(f"職人の保存に失敗しました: {e}") from e

    store = _get_dummy_workers()
    worker_id = _generate_worker_id_from_store(store)
    worker = {
        "worker_id": worker_id,
        "name": str(data.get("name", "")).strip(),
        "email": str(data.get("email", "")).strip(),
        "calendar_id": str(data.get("calendar_id", "")).strip(),
        "is_active": bool(data.get("is_active", True)),
        "role": str(data.get("role", "")).strip(),
        "note": str(data.get("note", "")).strip(),
        "display_order": int(data.get("display_order", 0)),
    }
    store.append(worker)
    return worker


def update_worker(worker_id: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """職人を更新."""
    client = try_get_firestore_client()
    if client:
        try:
            ref = client.collection("workers").document(worker_id)
            doc = ref.get()
            if not doc.exists:
                return None
            updated = {**doc.to_dict(), **data}
            ref.set(updated)
            return updated
        except FirestoreConnectionError:
            raise
        except Exception as e:
            raise FirestoreSaveError(f"職人の更新に失敗しました: {e}") from e

    store = _get_dummy_workers()
    for i, w in enumerate(store):
        if w.get("worker_id") == worker_id:
            store[i] = {**w, **data}
            return store[i]
    return None


def deactivate_worker(worker_id: str) -> Optional[Dict[str, Any]]:
    """職人を無効化."""
    return update_worker(worker_id, {"is_active": False})


def delete_worker(worker_id: str) -> bool:
    """職人ドキュメントを削除する。存在しなければ False."""
    client = try_get_firestore_client()
    if client:
        try:
            ref = client.collection("workers").document(worker_id)
            if not ref.get().exists:
                return False
            ref.delete()
            return True
        except FirestoreConnectionError:
            raise
        except Exception as e:
            raise FirestoreSaveError(f"職人の削除に失敗しました: {e}") from e

    store = _get_dummy_workers()
    for i, w in enumerate(store):
        if w.get("worker_id") == worker_id:
            store.pop(i)
            return True
    return False
