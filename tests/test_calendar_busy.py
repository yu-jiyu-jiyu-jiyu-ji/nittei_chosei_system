"""Google Calendar 占有判定のユニットテスト."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from services.calendar_service import event_counts_as_busy, interval_free_cached

TZ = ZoneInfo("Asia/Tokyo")


def _event(
    *,
    attendees=None,
    organizer=None,
    status=None,
) -> dict:
    ev = {}
    if attendees is not None:
        ev["attendees"] = attendees
    if organizer is not None:
        ev["organizer"] = organizer
    if status is not None:
        ev["status"] = status
    return ev


def test_needs_action_invitation_is_busy() -> None:
    ev = _event(
        attendees=[
            {
                "email": "worker@example.com",
                "self": True,
                "responseStatus": "needsAction",
            }
        ]
    )
    assert event_counts_as_busy(ev, owner_email="worker@example.com") is True


def test_declined_invitation_is_not_busy() -> None:
    ev = _event(
        attendees=[
            {
                "email": "worker@example.com",
                "self": True,
                "responseStatus": "declined",
            }
        ]
    )
    assert event_counts_as_busy(ev, owner_email="worker@example.com") is False


def test_accepted_invitation_is_busy() -> None:
    ev = _event(
        attendees=[
            {
                "email": "worker@example.com",
                "self": True,
                "responseStatus": "accepted",
            }
        ]
    )
    assert event_counts_as_busy(ev, owner_email="worker@example.com") is True


def test_organizer_event_is_busy() -> None:
    ev = _event(organizer={"email": "worker@example.com", "self": True})
    assert event_counts_as_busy(ev, owner_email="worker@example.com") is True


def test_cancelled_event_is_not_busy() -> None:
    ev = _event(status="cancelled", organizer={"email": "worker@example.com", "self": True})
    assert event_counts_as_busy(ev, owner_email="worker@example.com") is False


def test_interval_free_cached_requires_buffer_around_busy() -> None:
    busy = _event(
        organizer={"email": "other@example.com"},
        attendees=[
            {
                "email": "worker@example.com",
                "self": True,
                "responseStatus": "accepted",
            }
        ],
    )
    busy["start"] = {"dateTime": "2026-07-03T16:00:00+09:00"}
    busy["end"] = {"dateTime": "2026-07-03T17:00:00+09:00"}
    events = [busy]
    slot_start = datetime(2026, 7, 3, 17, 0, tzinfo=TZ)
    slot_end = datetime(2026, 7, 3, 19, 0, tzinfo=TZ)
    assert interval_free_cached(
        events, slot_start, slot_end, owner_email="worker@example.com"
    )
    assert not interval_free_cached(
        events,
        slot_start,
        slot_end,
        buffer_before_minutes=60,
        buffer_after_minutes=60,
        owner_email="worker@example.com",
    )
