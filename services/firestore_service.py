"""Firestore 接続の基盤サービス.

Firestore クライアントの初期化・取得を共通化する。
接続失敗時は適切な例外を送出する。
.env から GOOGLE_APPLICATION_CREDENTIALS を読み込む。
"""
from __future__ import annotations

from pathlib import Path

from utils.env_util import load_env_file

# .env を読み込み（プロジェクトルート = app.py があるディレクトリ）
_project_root = Path(__file__).resolve().parent.parent
load_env_file(_project_root / ".env")

from datetime import datetime
from typing import Any, Optional

from google.cloud import firestore
from google.cloud.firestore_v1 import Client as FirestoreClient


class FirestoreConnectionError(Exception):
    """Firestore 接続失敗."""

    pass


class FirestoreDataNotFoundError(Exception):
    """データ未登録."""

    pass


class FirestoreSaveError(Exception):
    """保存失敗."""

    pass


def _get_client_cached() -> Optional[FirestoreClient]:
    """Firestore クライアントを取得（初回のみ初期化、キャッシュ使用）."""
    cache_attr = "_firestore_client"
    if not hasattr(_get_client_cached, cache_attr):
        setattr(_get_client_cached, cache_attr, None)
    cached = getattr(_get_client_cached, cache_attr)
    if cached is not None:
        return cached
    try:
        client = firestore.Client()
        setattr(_get_client_cached, cache_attr, client)
        return client
    except Exception:
        return None


def get_firestore_client() -> FirestoreClient:
    """Firestore クライアントを取得.

    Raises:
        FirestoreConnectionError: 接続に失敗した場合
    """
    client = _get_client_cached()
    if client is None:
        raise FirestoreConnectionError(
            "Firestore に接続できません。"
            "GOOGLE_APPLICATION_CREDENTIALS が設定されているか確認してください。"
        )
    return client


def try_get_firestore_client() -> Optional[FirestoreClient]:
    """Firestore クライアントを取得（接続不可の場合は None を返す）."""
    return _get_client_cached()


def clear_client_cache() -> None:
    """クライアントキャッシュをクリア（テスト用）."""
    cache_attr = "_firestore_client"
    if hasattr(_get_client_cached, cache_attr):
        setattr(_get_client_cached, cache_attr, None)


def doc_to_dict(doc: Any) -> dict:
    """Firestore ドキュメントをアプリ用辞書に変換（timestamp → isoformat）."""
    data = doc.to_dict() if hasattr(doc, "to_dict") else dict(doc)
    result = {}
    for k, v in data.items():
        if isinstance(v, datetime):
            result[k] = v.isoformat()
        elif hasattr(v, "isoformat") and callable(getattr(v, "isoformat")):
            result[k] = v.isoformat()
        else:
            result[k] = v
    return result
