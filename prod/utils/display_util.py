from __future__ import annotations

from typing import List

import pandas as pd

from config.status_labels import STATUS_LABELS


def format_status(status: str) -> str:
    """ステータスの表示名に変換."""
    return STATUS_LABELS.get(status, status or "")


def projects_to_dataframe(projects: List[dict]) -> pd.DataFrame:
    """案件一覧表示用のDataFrameを生成."""
    if not projects:
        return pd.DataFrame(
            columns=[
                "案件ID",
                "案件名",
                "顧客名",
                "住所",
                "必要人数",
                "作業時間（分）",
                "必要車両数",
                "ステータス",
                "更新日時",
            ]
        )

    rows = []
    for p in projects:
        rows.append(
            {
                "案件ID": p.get("project_id"),
                "案件名": p.get("project_name"),
                "顧客名": p.get("customer_name"),
                "住所": p.get("address"),
                "必要人数": p.get("required_workers"),
                "作業時間（分）": p.get("work_duration_minutes"),
                "必要車両数": p.get("required_vehicle_count"),
                "ステータス": format_status(p.get("status", "")),
                "更新日時": p.get("updated_at"),
            }
        )

    return pd.DataFrame(rows)

