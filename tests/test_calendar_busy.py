"""Google Calendar 占有判定のユニットテスト."""
from __future__ import annotations

from services.calendar_service import event_counts_as_busy


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


def test_needs_action_invitation_is_not_busy() -> None:
    ev = _event(
        attendees=[
            {
                "email": "worker@example.com",
                "self": True,
                "responseStatus": "needsAction",
            }
        ]
    )
    assert event_counts_as_busy(ev, owner_email="worker@example.com") is False


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
