"""共通設定（settings）の読み書きサービス.

Firestore 必須。未接続時は FirestoreConnectionError を送出する。
settings コレクションは 1 ドキュメント（ID: system）で保持する。
"""
from __future__ import annotations

from typing import Any, Dict

from services.firestore_service import (
    FirestoreConnectionError,
    FirestoreSaveError,
    doc_to_dict,
    require_firestore_client,
)

DEFAULT_SETTINGS: Dict[str, Any] = {
    "setting_id": "system",
    "office_address": "東京都杉並区阿佐谷〇〇",
    "load_minutes": 20,
    "search_range_days": 90,
    "time_slot_minutes": 30,
    "max_candidate_count": 20,
    "traffic_buffer_morning_minutes": 20,
    "traffic_buffer_evening_minutes": 20,
    "traffic_buffer_morning_start": "07:00",
    "traffic_buffer_morning_end": "10:00",
    "traffic_buffer_evening_start": "16:00",
    "traffic_buffer_evening_end": "19:00",
    "work_hours_start": "07:00",
    "work_hours_end": "19:00",
    "google_vehicle_refresh_token": "",
}

SETTINGS_DOC_ID = "system"


def get_settings() -> Dict[str, Any]:
    """共通設定を取得."""
    client = require_firestore_client()
    try:
        ref = client.collection("settings").document(SETTINGS_DOC_ID)
        doc = ref.get()
        if not doc.exists:
            return dict(DEFAULT_SETTINGS)
        data = doc_to_dict(doc)
        result = dict(DEFAULT_SETTINGS)
        result.update(data)
        return result
    except FirestoreConnectionError:
        raise
    except Exception as e:
        raise FirestoreConnectionError(f"設定の取得に失敗しました: {e}") from e


def save_settings(data: Dict[str, Any]) -> Dict[str, Any]:
    """共通設定を保存."""
    client = require_firestore_client()
    try:
        ref = client.collection("settings").document(SETTINGS_DOC_ID)
        current = ref.get()
        base = current.to_dict() if current.exists else dict(DEFAULT_SETTINGS)
        updated = {**base, **data}
        ref.set(updated)
        return dict(updated)
    except FirestoreConnectionError:
        raise
    except Exception as e:
        raise FirestoreSaveError(f"設定の保存に失敗しました: {e}") from e


def reset_to_defaults() -> Dict[str, Any]:
    """初期値に戻す."""
    client = require_firestore_client()
    try:
        ref = client.collection("settings").document(SETTINGS_DOC_ID)
        ref.set(DEFAULT_SETTINGS)
        return dict(DEFAULT_SETTINGS)
    except FirestoreConnectionError:
        raise
    except Exception as e:
        raise FirestoreSaveError(f"設定のリセットに失敗しました: {e}") from e
