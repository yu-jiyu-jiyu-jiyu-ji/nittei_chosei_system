"""仮タイトル提案のユニットテスト."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from services.setting_service import (
    PROVISIONAL_TITLE_MODE_EXISTING,
    PROVISIONAL_TITLE_MODE_FORMAT,
)
from utils.provisional_title_util import (
    format_provisional_title,
    normalize_project_name_from_title,
    suggest_title_from_adjacent,
)

TZ = ZoneInfo("Asia/Tokyo")


def test_format_provisional_title_tokens() -> None:
    start = datetime(2026, 8, 12, 10, 30, tzinfo=TZ)
    assert (
        format_provisional_title("空き確保 YYYY/MM/DD HH:MM", start)
        == "空き確保 2026/08/12 10:30"
    )


def test_normalize_strips_field_prefix() -> None:
    assert normalize_project_name_from_title("[現場] ○○様 ガラス") == "○○様 ガラス"
    assert normalize_project_name_from_title("そのまま") == "そのまま"


def test_suggest_uses_adjacent_prev() -> None:
    start = datetime(2026, 8, 12, 10, 0, tzinfo=TZ)
    candidate = {
        "worker_ids": ["w1"],
        "worker_adjacent_events": {
            "w1": {
                "prev": {"summary": "[移動] 拠点→現場", "time": "09:00〜09:30", "location": "x"},
                "next": {"summary": "田中様 内窓", "time": "14:00〜16:00", "location": "y"},
            }
        },
    }
    title, note = suggest_title_from_adjacent(
        candidate,
        start_at=start,
        settings={"provisional_title_mode": PROVISIONAL_TITLE_MODE_EXISTING},
    )
    assert title == "田中様 内窓"
    assert "既存" in note or "参考" in note


def test_suggest_falls_back_to_format() -> None:
    start = datetime(2026, 8, 12, 10, 0, tzinfo=TZ)
    candidate = {
        "worker_ids": ["w1"],
        "worker_adjacent_events": {
            "w1": {
                "prev": {"summary": "[移動] 拠点→現場"},
            }
        },
    }
    title, note = suggest_title_from_adjacent(
        candidate,
        start_at=start,
        settings={
            "provisional_title_mode": PROVISIONAL_TITLE_MODE_EXISTING,
            "provisional_title_format": "空き確保 YYYY/MM/DD HH:MM",
        },
    )
    assert title == "空き確保 2026/08/12 10:00"
    assert "書式" in note


def test_suggest_format_mode_ignores_adjacent() -> None:
    start = datetime(2026, 8, 12, 10, 0, tzinfo=TZ)
    candidate = {
        "worker_ids": ["w1"],
        "worker_adjacent_events": {
            "w1": {"prev": {"summary": "田中様 内窓"}},
        },
    }
    title, note = suggest_title_from_adjacent(
        candidate,
        start_at=start,
        settings={
            "provisional_title_mode": PROVISIONAL_TITLE_MODE_FORMAT,
            "provisional_title_format": "仮 YYYY/MM/DD",
        },
    )
    assert title == "仮 2026/08/12"
    assert note == "設定の書式"
