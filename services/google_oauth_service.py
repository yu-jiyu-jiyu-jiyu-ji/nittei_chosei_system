"""Google OAuth（Calendar API）URL生成・トークン交換."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

# 空き検索（読取）と候補確定時の予定作成（書込）の両方に対応
CALENDAR_EVENTS = "https://www.googleapis.com/auth/calendar.events"

# OAuth の state に使う。職人の worker_id と衝突しない値。
OAUTH_STATE_VEHICLE_FLEET = "vehicle_fleet"

_SCOPES = [CALENDAR_EVENTS]


def oauth_client_configured() -> bool:
    cid = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "").strip()
    csec = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
    return bool(cid and csec)


def get_redirect_uri() -> str:
    return os.environ.get("GOOGLE_OAUTH_REDIRECT_URI", "http://localhost:8501/oauth_calendar_return").strip()


def _client_config() -> Dict[str, Any]:
    cid = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "").strip()
    csec = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
    return {
        "web": {
            "client_id": cid,
            "client_secret": csec,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }


def build_authorization_url(state: str) -> Optional[str]:
    """職人連携用の Google 認証 URL を返す."""
    if not oauth_client_configured():
        return None
    flow = Flow.from_client_config(
        _client_config(),
        scopes=_SCOPES,
        redirect_uri=get_redirect_uri(),
    )
    # include_granted_scopes=True にすると、過去に付いた calendar.readonly 等がマージされ、
    # トークン応答の scope 文字列が _SCOPES のみと一致せず「Scope has changed」で交換失敗することがある。
    url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        state=state,
    )
    return url


def exchange_code_for_credentials(code: str) -> tuple[Optional[Credentials], Optional[str]]:
    """認証コードをトークンに交換。失敗時は (None, 人間向け理由)."""
    if not oauth_client_configured():
        return None, "OAuth クライアント ID/シークレットが未設定です。"
    flow = Flow.from_client_config(
        _client_config(),
        scopes=_SCOPES,
        redirect_uri=get_redirect_uri(),
    )
    try:
        flow.fetch_token(code=code)
        return flow.credentials, None
    except Exception as e:
        return None, str(e)


def credentials_from_refresh_token(refresh_token: str) -> Optional[Credentials]:
    """リフレッシュトークンから Credentials を構築（有効期限切れ時は refresh が必要）.

    scopes は None にする。リフレッシュ要求に固定スコープを付けると、以前
    calendar.readonly 等で取得したトークンと食い違い、invalid_scope で失敗する。
    スコープ未指定のリフレッシュでは、発行時に付与されたスコープがそのまま使われる。
    予定の新規作成が必要な場合は、共通設定から該当アカウントの連携をやり直し、
    calendar.events を付与したトークンを取り直すこと。
    """
    if not refresh_token or not oauth_client_configured():
        return None
    cfg = _client_config()["web"]
    return Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=cfg["token_uri"],
        client_id=cfg["client_id"],
        client_secret=cfg["client_secret"],
        scopes=None,
    )


def refresh_if_needed(creds: Credentials) -> Credentials:
    from google.auth.transport.requests import Request

    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return creds


def load_vehicle_token_json_path(path: str) -> Optional[Credentials]:
    """車両用アカウントのトークン JSON（oauth2 ツール等で取得）を読み込む."""
    p = path.strip()
    if not p:
        return None
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        rt = data.get("refresh_token")
        if not rt:
            return None
        return credentials_from_refresh_token(rt)
    except OSError:
        return None
