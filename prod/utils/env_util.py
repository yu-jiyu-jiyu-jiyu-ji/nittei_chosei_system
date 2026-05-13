"""プロジェクトルートの .env を標準ライブラリのみで読み込む."""

from __future__ import annotations

import os
from pathlib import Path


def load_env_file(path: Path) -> None:
    """KEY=VALUE 形式の .env を読み、未設定の環境変数のみ os.environ に反映する."""
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value
