"""calendar_collect_service のユニットテスト."""

from __future__ import annotations

from datetime import date

from services.calendar_collect_service import (
    event_visible_for_collect,
    format_collect_event_times,
    format_member_display_names,
)


def test_event_visible_for_collect_hides_cancelled() -> None:
    assert event_visible_for_collect({"status": "cancelled"}, owner_email="a@x.com") is False


def test_event_visible_for_collect_hides_declined_invite() -> None:
    ev = {
        "status": "confirmed",
        "attendees": [{"email": "a@x.com", "responseStatus": "declined"}],
    }
    assert event_visible_for_collect(ev, owner_email="a@x.com") is False


def test_event_visible_for_collect_shows_needs_action() -> None:
    ev = {
        "status": "confirmed",
        "attendees": [{"email": "a@x.com", "responseStatus": "needsAction"}],
    }
    assert event_visible_for_collect(ev, owner_email="a@x.com") is True


def test_format_member_display_names() -> None:
    ev = {
        "organizer": {"displayName": "主催者"},
        "attendees": [
            {"displayName": "田中"},
            {"email": "suzuki@example.com"},
        ],
    }
    assert format_member_display_names(ev) == "主催者、田中、suzuki"


def test_format_collect_event_times_all_day() -> None:
    ev = {
        "start": {"date": "2026-06-05"},
        "end": {"date": "2026-06-06"},
    }
    date_s, start_s, end_s = format_collect_event_times(ev)
    assert date_s == "2026/06/05"
    assert start_s == "終日"
    assert end_s == "終日"


def test_format_collect_event_times_timed() -> None:
    ev = {
        "start": {"dateTime": "2026-06-05T01:00:00Z"},
        "end": {"dateTime": "2026-06-05T03:00:00Z"},
    }
    date_s, start_s, end_s = format_collect_event_times(ev)
    assert date_s == "2026/06/05"
    assert start_s
    assert end_s
