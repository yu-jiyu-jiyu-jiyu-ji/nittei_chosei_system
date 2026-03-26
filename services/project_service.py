"""案件（projects）の読み書きサービス.

Firestore を優先し、接続不可時はダミーストアにフォールバックする。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

import streamlit as st

from config.constants import CONSTRUCTION_TYPE_OTHER
from models.project_model import Project
from services.firestore_service import (
    FirestoreConnectionError,
    FirestoreDataNotFoundError,
    FirestoreSaveError,
    doc_to_dict,
    try_get_firestore_client,
)


def _get_initial_dummy_projects() -> List[Dict[str, Any]]:
    """初期ダミー案件一覧（動作確認用）."""
    now = datetime.utcnow().isoformat()
    return [
        {
            "project_id": "PJT_0001",
            "project_name": "〇〇ビル ガラス交換",
            "customer_name": "〇〇株式会社",
            "address": "東京都台東区1-2-3",
            "required_workers": 2,
            "work_duration_minutes": 120,
            "required_vehicle_count": 1,
            "vehicle_decision_type": "auto",
            "construction_type": ["ガラス交換"],
            "construction_type_other": None,
            "note": "午前希望",
            "status": "draft",
            "created_at": now,
            "updated_at": now,
            "created_by": "開発ユーザー",
            "updated_by": "開発ユーザー",
        },
        {
            "project_id": "PJT_0002",
            "project_name": "△△マンション 外壁補修",
            "customer_name": "△△不動産",
            "address": "東京都杉並区4-5-6",
            "required_workers": 3,
            "work_duration_minutes": 180,
            "required_vehicle_count": 1,
            "vehicle_decision_type": "auto",
            "construction_type": ["その他"],
            "construction_type_other": "外壁補修",
            "note": "終日可",
            "status": "confirmed",
            "created_at": now,
            "updated_at": now,
            "created_by": "開発ユーザー",
            "updated_by": "開発ユーザー",
        },
        {
            "project_id": "PJT_0003",
            "project_name": "□□邸 リフォーム",
            "customer_name": "□□様",
            "address": "東京都世田谷区7-8-9",
            "required_workers": 4,
            "work_duration_minutes": 240,
            "required_vehicle_count": 2,
            "vehicle_decision_type": "manual",
            "construction_type": ["窓交換"],
            "construction_type_other": None,
            "note": "午後希望",
            "status": "draft",
            "created_at": now,
            "updated_at": now,
            "created_by": "開発ユーザー",
            "updated_by": "開発ユーザー",
        },
    ]


def _get_project_store() -> List[Dict[str, Any]]:
    """案件のダミーストアを取得（Firestore 不可時のフォールバック）."""
    if "dummy_projects" not in st.session_state:
        st.session_state["dummy_projects"] = _get_initial_dummy_projects()
    return st.session_state["dummy_projects"]


def _generate_project_id_from_store(store: List[Dict[str, Any]]) -> str:
    """ストアの件数から案件ID採番."""
    next_number = len(store) + 1
    return f"PJT_{next_number:04d}"


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
        "vehicle_decision_type": data.get("vehicle_decision_type") or None,
        "construction_type": construction_type,
        "construction_type_other": construction_type_other_val,
        "note": str(data.get("note") or "").strip(),
    }


def create_project(
    data: Dict[str, Any],
    current_user_name: Optional[str] = None,
) -> Dict[str, Any]:
    """案件を新規作成し、Firestore またはダミーストアに保存."""
    client = try_get_firestore_client()
    if client:
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
            # Firestore は datetime をそのまま受け付ける
            coll = client.collection("projects")
            coll.document(project_id).set(project)
            return {**project, "created_at": now.isoformat(), "updated_at": now.isoformat()}
        except FirestoreConnectionError:
            raise
        except Exception as e:
            raise FirestoreSaveError(f"案件の保存に失敗しました: {e}") from e

    # フォールバック: ダミー
    store = _get_project_store()
    project_id = _generate_project_id_from_store(store)
    now = datetime.utcnow()
    base = _prepare_project_data(data)
    project = Project(
        project_id=project_id,
        project_name=base["project_name"],
        customer_name=base["customer_name"],
        address=base["address"],
        required_workers=base["required_workers"],
        work_duration_minutes=base["work_duration_minutes"],
        required_vehicle_count=base["required_vehicle_count"],
        vehicle_decision_type=base["vehicle_decision_type"],
        construction_type=base["construction_type"],
        construction_type_other=base["construction_type_other"],
        note=base["note"],
        status="draft",
        created_at=now,
        updated_at=now,
        created_by=current_user_name,
        updated_by=current_user_name,
    )
    store.append(project.to_dict())
    return project.to_dict()


def update_project(
    project_id: str,
    data: Dict[str, Any],
    current_user_name: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """案件を更新."""
    client = try_get_firestore_client()
    if client:
        try:
            ref = client.collection("projects").document(project_id)
            doc = ref.get()
            if not doc.exists:
                return None
            now = datetime.utcnow()
            base = _prepare_project_data(data)
            updated = {
                **doc.to_dict(),
                **base,
                "updated_at": now,
                "updated_by": current_user_name,
            }
            ref.set(updated)
            return {**updated, "updated_at": now.isoformat(), "updated_by": current_user_name}
        except FirestoreConnectionError:
            raise
        except Exception as e:
            raise FirestoreSaveError(f"案件の更新に失敗しました: {e}") from e

    # フォールバック
    store = _get_project_store()
    now = datetime.utcnow().isoformat()
    for i, p in enumerate(store):
        if p.get("project_id") == project_id:
            construction_type_raw = data.get("construction_type", p.get("construction_type", []))
            if isinstance(construction_type_raw, list):
                construction_type = [str(x).strip() for x in construction_type_raw if x]
            else:
                construction_type = [str(construction_type_raw).strip()] if construction_type_raw else []
            if CONSTRUCTION_TYPE_OTHER in construction_type:
                construction_type_other_val = str(data.get("construction_type_other") or "").strip()
                if construction_type_other_val == "":
                    construction_type_other_val = p.get("construction_type_other")
            else:
                construction_type_other_val = None

            updated = {
                **p,
                "project_name": str(data.get("project_name", p.get("project_name", ""))).strip(),
                "customer_name": str(data.get("customer_name", p.get("customer_name", ""))).strip(),
                "address": str(data.get("address", p.get("address", ""))).strip(),
                "required_workers": int(data.get("required_workers", p.get("required_workers", 0))),
                "work_duration_minutes": int(
                    data.get("work_duration_minutes", p.get("work_duration_minutes", 0))
                ),
                "required_vehicle_count": (
                    int(data["required_vehicle_count"])
                    if data.get("required_vehicle_count") not in (None, "")
                    else p.get("required_vehicle_count")
                ),
                "vehicle_decision_type": data.get("vehicle_decision_type") or p.get("vehicle_decision_type"),
                "construction_type": construction_type,
                "construction_type_other": construction_type_other_val,
                "note": str(data.get("note", p.get("note", ""))).strip(),
                "status": data.get("status", p.get("status", "draft")),
                "updated_at": now,
                "updated_by": current_user_name,
            }
            store[i] = updated
            return updated
    return None


def patch_project_fields(
    project_id: str,
    fields: Dict[str, Any],
    current_user_name: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """案件ドキュメントに任意フィールドのみマージ（予定確定時の scheduled_* など）."""
    if not fields:
        return None
    client = try_get_firestore_client()
    if client:
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

    store = _get_project_store()
    now = datetime.utcnow().isoformat()
    for i, p in enumerate(store):
        if p.get("project_id") == project_id:
            merged = {**p, **fields, "updated_at": now, "updated_by": current_user_name}
            store[i] = merged
            return merged
    return None


def list_projects(filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """案件一覧を取得（Firestore 優先、フィルタ付き）."""
    client = try_get_firestore_client()
    if client:
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
    else:
        projects = list(_get_project_store())

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
