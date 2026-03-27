"""アプリ用ファイルログ（サーバー上の logs/app.log に追記）.

画面からは参照しない。運用・障害調査時にエンジニアがファイルを直接確認する想定。
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Optional

_lock = Lock()


def _log_path() -> Path:
    root = Path(__file__).resolve().parent.parent
    d = root / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d / "app.log"


def append_app_log(message: str, *, extra: Optional[Dict[str, Any]] = None) -> None:
    """1行テキストを logs/app.log に追記する。"""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"{ts}\t{message}"
    if extra:
        line += f"\t{extra!r}"
    line += "\n"
    try:
        with _lock:
            with open(_log_path(), "a", encoding="utf-8") as f:
                f.write(line)
    except OSError:
        pass
