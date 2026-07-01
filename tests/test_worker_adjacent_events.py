"""職人の直前・直後予定パッケージのユニットテスト."""
from __future__ import annotations

from services.candidate_search_service import _pack_adjacent_event


def test_pack_adjacent_event_without_location_shows_placeholder() -> None:
    ev = {
        "summary": "前現場",
        "start": {"dateTime": "2026-07-03T10:00:00+09:00"},
        "end": {"dateTime": "2026-07-03T11:00:00+09:00"},
    }
    packed = _pack_adjacent_event(ev, worker_id="w1", loc_ov={})
    assert packed is not None
    assert packed["location"] == "住所なし"
    assert packed["summary"] == "前現場"


def test_pack_adjacent_event_uses_location_override() -> None:
    ev = {
        "id": "evt1",
        "summary": "前現場",
        "start": {"dateTime": "2026-07-03T10:00:00+09:00"},
        "end": {"dateTime": "2026-07-03T11:00:00+09:00"},
        "location": "",
    }
    packed = _pack_adjacent_event(
        ev,
        worker_id="w1",
        loc_ov={"w1:evt1": "東京都千代田区1-1"},
    )
    assert packed is not None
    assert packed["location"] == "東京都千代田区1-1"
