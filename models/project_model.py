from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class Project:
    """projects コレクション相当の最小モデル."""

    project_id: str
    project_name: str
    customer_name: str
    address: str
    required_workers: int
    work_duration_minutes: int
    required_vehicle_count: Optional[int] = None
    vehicle_decision_type: Optional[str] = None
    construction_type: List[str] = field(default_factory=list)
    construction_type_other: Optional[str] = None
    note: str = ""
    status: str = "draft"
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    created_by: Optional[str] = None
    updated_by: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Firestore保存を想定した辞書形式への変換.

        Phase1 ではダミーデータとしてのみ使用。
        """
        return {
            "project_id": self.project_id,
            "project_name": self.project_name,
            "customer_name": self.customer_name,
            "address": self.address,
            "required_workers": self.required_workers,
            "required_vehicle_count": self.required_vehicle_count,
            "vehicle_decision_type": self.vehicle_decision_type,
            "construction_type": self.construction_type,
            "construction_type_other": self.construction_type_other,
            "work_duration_minutes": self.work_duration_minutes,
            "note": self.note,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "created_by": self.created_by,
            "updated_by": self.updated_by,
        }

