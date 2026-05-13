"""案件（projects）の読み書きサービス.

Firestore 必須。未接続時は FirestoreConnectionError を送出する。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from config.constants import CONSTRUCTION_TYPE_OTHER
from config.status_labels import STATUS_LABELS
from services.firestore_service import (
    FirestoreConnectionError,
    FirestoreSaveError,
    doc_to_dict,
    require_firestore_client,
)


def _prepare_project_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """フォームデータを案件用辞書に変換."""
    construction_type_raw = data.get("construction_type")
    if isinstance(construction_type_raw, list):
        construction_type = [str(x).strip() for x in construction_type_raw if x]
    else:
        construction_type = [str(construction_type_raw).strip()] if construction_type_raw else []
    construction_type_other_val = (
        str(data.get("construction_type_other") or "").strip()
        if CONSTRUCTION_TYPE_OTHER in construction_type
        else None
    )
    if construction_type_other_val == "":
        construction_type_other_val = None

    return {
        "project_name": str(data["project_name"]).strip(),
        "customer_name": str(data["customer_name"]).strip(),
        "address": str(data["address"]).strip(),
        "required_workers": int(data["required_workers"]),
        "work_duration_minutes": int(data["work_duration_minutes"]),
        "required_vehicle_count": (
            int(data["required_vehicle_count"])
            if data.get("required_vehicle_count") not in (None, "")
            else None
        ),
        "construction_type": construction_type,
        "construction_type_other": construction_type_other_val,
        "note": str(data.get("note") or "").strip(),
    }


def _generate_project_id_firestore(client: Any) -> str:
    """Firestore の件数から案件ID採番."""
    coll = client.collection("projects")
    docs = list(coll.stream())
    if not docs:
        return "PJT_0001"
    numbers = []
    for d in docs:
        data = d.to_dict()
        pid = data.get("project_id", "")
        if pid.startswith("PJT_") and len(pid) >= 8:
            try:
                numbers.append(int(pid[4:8]))
            except ValueError:
                pass
    next_number = max(numbers, default=0) + 1
    return f"PJT_{next_number:04d}"


def create_project(
    data: Dict[str, Any],
    current_user_name: Optional[str] = None,
) -> Dict[str, Any]:
    """案件を新規作成."""
    client = require_firestore_client()
    try:
        project_id = _generate_project_id_firestore(client)
        now = datetime.utcnow()
        base = _prepare_project_data(data)
        project = {
            "project_id": project_id,
            **base,
            "status": "draft",
            "created_at": now,
            "updated_at": now,
            "created_by": current_user_name,
            "updated_by": current_user_name,
        }
        coll = client.collection("projects")
        coll.document(project_id).set(project)
        return {**project, "created_at": now.isoformat(), "updated_at": now.isoformat()}
    except FirestoreConnectionError:
        raise
    except Exception as e:
        raise FirestoreSaveError(f"案件の保存に失敗しました: {e}") from e


def update_project(
    project_id: str,
    data: Dict[str, Any],
    current_user_name: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """案件を更新."""
    client = require_firestore_client()
    try:
        ref = client.collection("projects").document(project_id)
        doc = ref.get()
        if not doc.exists:
            return None
        now = datetime.utcnow()
        base = _prepare_project_data(data)
        doc_data = doc.to_dict() or {}
        updated = {
            **doc_data,
            **base,
            "updated_at": now,
            "updated_by": current_user_name,
        }
        if "status" in data:
            s = str(data.get("status") or "").strip()
            if s in STATUS_LABELS:
                updated["status"] = s
        ref.set(updated)
        return {**updated, "updated_at": now.isoformat(), "updated_by": current_user_name}
    except FirestoreConnectionError:
        raise
    except Exception as e:
        raise FirestoreSaveError(f"案件の更新に失敗しました: {e}") from e


def delete_project(project_id: str, current_user_name: Optional[str] = None) -> bool:
    """案件を削除。存在しなければ False。"""
    _ = current_user_name  # 将来の監査ログ用に予約
    client = require_firestore_client()
    try:
        ref = client.collection("projects").document(project_id)
        if not ref.get().exists:
            return False
        ref.delete()
        return True
    except FirestoreConnectionError:
        raise
    except Exception as e:
        raise FirestoreSaveError(f"案件の削除に失敗しました: {e}") from e


def patch_project_fields(
    project_id: str,
    fields: Dict[str, Any],
    current_user_name: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """案件ドキュメントに任意フィールドのみマージ（予定確定時の scheduled_* など）."""
    if not fields:
        return None
    client = require_firestore_client()
    try:
        ref = client.collection("projects").document(project_id)
        doc = ref.get()
        if not doc.exists:
            return None
        now = datetime.utcnow()
        base = doc.to_dict() or {}
        updated = {
            **base,
            **fields,
            "updated_at": now,
            "updated_by": current_user_name,
        }
        ref.set(updated)
        return {**updated, "updated_at": now.isoformat(), "updated_by": current_user_name}
    except FirestoreConnectionError:
        raise
    except Exception as e:
        raise FirestoreSaveError(f"案件の更新に失敗しました: {e}") from e


def list_projects(filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """案件一覧を取得（フィルタ付き）."""
    client = require_firestore_client()
    try:
        coll = client.collection("projects")
        docs = list(coll.stream())
        projects = []
        for d in docs:
            data = doc_to_dict(d)
            data["project_id"] = data.get("project_id") or d.id
            projects.append(data)
    except FirestoreConnectionError:
        raise
    except Exception as e:
        raise FirestoreConnectionError(f"案件一覧の取得に失敗しました: {e}") from e

    filters = filters or {}
    project_name = (filters.get("project_name") or "").strip()
    customer_name = (filters.get("customer_name") or "").strip()
    status = (filters.get("status") or "").strip()

    def matches(p: Dict[str, Any]) -> bool:
        if project_name and project_name not in str(p.get("project_name", "")):
            return False
        if customer_name and customer_name not in str(p.get("customer_name", "")):
            return False
        if status and status != p.get("status"):
            return False
        return True

    filtered = [p for p in projects if matches(p)]
    filtered.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
    return filtered
