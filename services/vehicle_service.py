"""車両（vehicles）の読み書きサービス.

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

VEHICLE_STATUS = {"available": "利用可能", "maintenance": "点検中", "unavailable": "利用不可"}


def _get_dummy_vehicles() -> List[Dict[str, Any]]:
    """車両のダミーデータ（フォールバック用）."""
    if "dummy_vehicles" not in st.session_state:
        st.session_state["dummy_vehicles"] = [
            {
                "vehicle_id": "V001",
                "name": "2人乗り1号車",
                "email": "",
                "capacity": 2,
                "calendar_id": "car1@example.com",
                "is_active": True,
                "status": "available",
                "note": "",
                "display_order": 1,
            },
            {
                "vehicle_id": "V002",
                "name": "3人乗り1号車",
                "email": "",
                "capacity": 3,
                "calendar_id": "car2@example.com",
                "is_active": True,
                "status": "available",
                "note": "",
                "display_order": 2,
            },
            {
                "vehicle_id": "V003",
                "name": "4人乗り1号車",
                "email": "",
                "capacity": 4,
                "calendar_id": "car3@example.com",
                "is_active": True,
                "status": "available",
                "note": "",
                "display_order": 3,
            },
        ]
    return st.session_state["dummy_vehicles"]


def _generate_vehicle_id_from_store(store: List[Dict[str, Any]]) -> str:
    """ストアから車両ID採番."""
    next_num = max((int(v.get("vehicle_id", "V000")[1:]) for v in store), default=0) + 1
    return f"V{next_num:03d}"


def _generate_vehicle_id_firestore(client: Any) -> str:
    """Firestore から車両ID採番."""
    coll = client.collection("vehicles")
    docs = list(coll.stream())
    if not docs:
        return "V001"
    numbers = []
    for d in docs:
        data = d.to_dict()
        vid = data.get("vehicle_id", "") or d.id
        if isinstance(vid, str) and vid.startswith("V") and len(vid) >= 2:
            try:
                numbers.append(int(vid[1:]))
            except ValueError:
                pass
    next_num = max(numbers, default=0) + 1
    return f"V{next_num:03d}"


def list_vehicles() -> List[Dict[str, Any]]:
    """車両一覧を取得."""
    client = try_get_firestore_client()
    if client:
        try:
            coll = client.collection("vehicles")
            docs = list(coll.stream())
            vehicles = []
            for d in docs:
                data = doc_to_dict(d)
                data["vehicle_id"] = data.get("vehicle_id") or d.id
                vehicles.append(data)
        except FirestoreConnectionError:
            raise
        except Exception as e:
            raise FirestoreConnectionError(f"車両一覧の取得に失敗しました: {e}") from e
    else:
        vehicles = list(_get_dummy_vehicles())

    vehicles.sort(key=lambda x: (x.get("display_order", 999), x.get("vehicle_id", "")))
    return vehicles


def create_vehicle(data: Dict[str, Any]) -> Dict[str, Any]:
    """車両を新規作成."""
    client = try_get_firestore_client()
    if client:
        try:
            vehicle_id = _generate_vehicle_id_firestore(client)
            vehicle = {
                "vehicle_id": vehicle_id,
                "name": str(data.get("name", "")).strip(),
                "email": str(data.get("email", "")).strip(),
                "capacity": int(data.get("capacity", 1)),
                "calendar_id": str(data.get("calendar_id", "")).strip(),
                "is_active": bool(data.get("is_active", True)),
                "status": str(data.get("status", "available")),
                "note": str(data.get("note", "")).strip(),
                "display_order": int(data.get("display_order", 0)),
            }
            client.collection("vehicles").document(vehicle_id).set(vehicle)
            return vehicle
        except FirestoreConnectionError:
            raise
        except Exception as e:
            raise FirestoreSaveError(f"車両の保存に失敗しました: {e}") from e

    store = _get_dummy_vehicles()
    vehicle_id = _generate_vehicle_id_from_store(store)
    vehicle = {
        "vehicle_id": vehicle_id,
        "name": str(data.get("name", "")).strip(),
        "email": str(data.get("email", "")).strip(),
        "capacity": int(data.get("capacity", 1)),
        "calendar_id": str(data.get("calendar_id", "")).strip(),
        "is_active": bool(data.get("is_active", True)),
        "status": str(data.get("status", "available")),
        "note": str(data.get("note", "")).strip(),
        "display_order": int(data.get("display_order", 0)),
    }
    store.append(vehicle)
    return vehicle


def update_vehicle(vehicle_id: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """車両を更新."""
    client = try_get_firestore_client()
    if client:
        try:
            ref = client.collection("vehicles").document(vehicle_id)
            doc = ref.get()
            if not doc.exists:
                return None
            updated = {**doc.to_dict(), **data}
            ref.set(updated)
            return updated
        except FirestoreConnectionError:
            raise
        except Exception as e:
            raise FirestoreSaveError(f"車両の更新に失敗しました: {e}") from e

    store = _get_dummy_vehicles()
    for i, v in enumerate(store):
        if v.get("vehicle_id") == vehicle_id:
            store[i] = {**v, **data}
            return store[i]
    return None


def deactivate_vehicle(vehicle_id: str) -> Optional[Dict[str, Any]]:
    """車両を無効化."""
    return update_vehicle(vehicle_id, {"is_active": False})


def delete_vehicle(vehicle_id: str) -> bool:
    """車両ドキュメントを削除する。存在しなければ False."""
    client = try_get_firestore_client()
    if client:
        try:
            ref = client.collection("vehicles").document(vehicle_id)
            if not ref.get().exists:
                return False
            ref.delete()
            return True
        except FirestoreConnectionError:
            raise
        except Exception as e:
            raise FirestoreSaveError(f"車両の削除に失敗しました: {e}") from e

    store = _get_dummy_vehicles()
    for i, v in enumerate(store):
        if v.get("vehicle_id") == vehicle_id:
            store.pop(i)
            return True
    return False
