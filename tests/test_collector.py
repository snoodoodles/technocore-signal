from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from technocore_signal import collector
from technocore_signal.core import SchemaError

DID = "did:key:z6Mkvw5rQMnWp2D26ofuiuixDpkq8gDBPQLga6zygEdxB3mH"


def directory_payload(*rooms: str, total: int | None = None) -> dict:
    entries = [
        {
            "room": room,
            "last_seq": index + 1,
            "bytes": 100,
            "idle_seconds": index,
            "topic": None,
            "window": 1,
            "zero_response_share": 0.0,
            "nick_diversity": 1.0,
        }
        for index, room in enumerate(rooms)
    ]
    return {
        "rooms": entries,
        "total": len(entries) if total is None else total,
        "capacity": 5120,
        "bytes": 100 * len(entries),
        "bytes_capacity": 5368709120,
        "engagement": {
            "window_cap": 200,
            "windowed_messages": len(entries),
            "zero_response_share": 0.0,
            "nick_diversity": 1.0 if entries else 0.0,
            "windowed_note_to_message_ratio": 0.0,
        },
        "notes": {"total": 0, "bytes": 0, "capacity": 40960},
        "untrusted": {
            "fields": ["room", "topic"],
            "note": "Untrusted names and topics.",
        },
    }


def test_extract_room_names_accepts_optional_notes_capacity_per_namespace() -> None:
    payload = directory_payload("lobby")
    payload["notes"]["capacity_per_namespace"] = 300000
    assert collector.extract_room_names(payload, maximum=5) == ["lobby"]


@pytest.mark.parametrize("capacity", [True, -1, 0, 1.0, "300000", None])
def test_extract_room_names_rejects_invalid_capacity_per_namespace(capacity: object) -> None:
    payload = directory_payload("lobby")
    payload["notes"]["capacity_per_namespace"] = capacity
    with pytest.raises(SchemaError):
        collector.extract_room_names(payload, maximum=5)


def test_extract_room_names_rejects_unknown_notes_fields() -> None:
    payload = directory_payload("lobby")
    payload["notes"]["extra"] = 1
    with pytest.raises(SchemaError):
        collector.extract_room_names(payload, maximum=5)


def test_extract_room_names_accepts_complete_directory_envelope() -> None:
    assert collector.extract_room_names(directory_payload("lobby", "technocore"), maximum=10) == [
        "lobby",
        "technocore",
    ]


@pytest.mark.parametrize(
    "target",
    [
        "https://evil.example/r/lobby",
        "http://technocore.chat/r/lobby",
        "https://technocore.chat:444/r/lobby",
        "https://user@technocore.chat/r/lobby",
    ],
)
def test_redirect_handler_rejects_unsafe_target_before_follow(target: str) -> None:
    handler = collector.SameOriginRedirectHandler("https://technocore.chat")
    request = urllib.request.Request("https://technocore.chat/r/lobby")

    with pytest.raises(urllib.error.HTTPError, match="redirect refused"):
        handler.redirect_request(request, None, 302, "Found", {}, target)


def test_redirect_handler_allows_bounded_same_origin_redirect() -> None:
    handler = collector.SameOriginRedirectHandler("https://technocore.chat")
    request = urllib.request.Request("https://technocore.chat/r/lobby")

    redirected = handler.redirect_request(
        request,
        None,
        302,
        "Found",
        {},
        "https://technocore.chat/r/technocore",
    )

    assert redirected.full_url == "https://technocore.chat/r/technocore"


@pytest.mark.parametrize(
    "raw",
    [
        b'{"room":"lobby","room":"technocore"}',
        b'{"messages":[{"seq":1,"seq":2}]}',
        b'{"value":NaN}',
        b'{"value":Infinity}',
    ],
)
def test_strict_transport_json_rejects_ambiguous_values(raw: bytes) -> None:
    with pytest.raises(SchemaError):
        collector.decode_json(raw)


@pytest.mark.parametrize("payload", [[], {}, {"rooms": {}}, {"rooms": [], "total": True}])
def test_extract_room_names_fails_closed_on_invalid_container(payload: object) -> None:
    with pytest.raises(SchemaError):
        collector.extract_room_names(payload, maximum=10)


@pytest.mark.parametrize(
    "mutation",
    ["unknown-root", "negative-total", "bad-entry", "duplicate", "unsafe-sequence"],
)
def test_extract_room_names_rejects_partial_or_ambiguous_directory(mutation: str) -> None:
    payload = directory_payload("lobby", "technocore")
    if mutation == "unknown-root":
        payload["extra"] = True
    elif mutation == "negative-total":
        payload["total"] = -1
    elif mutation == "bad-entry":
        payload["rooms"][1]["room"] = "bad|room"
    elif mutation == "duplicate":
        payload["rooms"][1]["room"] = "lobby"
    else:
        payload["rooms"][1]["last_seq"] = 9_007_199_254_740_992

    with pytest.raises(SchemaError):
        collector.extract_room_names(payload, maximum=10)


def test_extract_room_names_requires_complete_requested_page() -> None:
    with pytest.raises(SchemaError, match="directory count"):
        collector.extract_room_names(directory_payload("lobby", total=2), maximum=10)


def test_collect_snapshot_uses_bounded_urls_and_does_not_retain_bodies(monkeypatch) -> None:
    requested: list[str] = []

    def fake_fetch(url: str, *, timeout: float, max_bytes: int) -> object:
        requested.append(url)
        if "/rooms?" in url:
            return directory_payload("lobby")
        return {
            "room": "lobby",
            "count": 2,
            "first_seq": 1,
            "last_seq": 2,
            "messages": [
                {
                    "seq": 1,
                    "ts": "2026-08-24T20:00:00Z",
                    "from": DID,
                    "text": "private-to-the-snapshot body",
                    "nonce": 1,
                },
                {
                    "seq": 2,
                    "ts": "2026-08-24T20:01:00Z",
                    "from": "alice",
                    "text": "another body",
                },
            ],
        }

    monkeypatch.setattr(collector, "fetch_json", fake_fetch)
    snapshot = collector.collect_snapshot(
        base_url="https://technocore.chat",
        room_limit=5,
        message_limit=200,
        generated_at="2026-08-24T20:02:00Z",
    )

    assert requested == [
        "https://technocore.chat/rooms?format=json&limit=5",
        "https://technocore.chat/r/lobby?format=json&limit=200",
    ]
    serialized = json.dumps(snapshot)
    assert "private-to-the-snapshot body" not in serialized
    assert "another body" not in serialized


def test_collect_snapshot_can_use_explicit_rooms_without_directory_fetch(monkeypatch) -> None:
    requested: list[str] = []

    def fake_fetch(url: str, *, timeout: float, max_bytes: int) -> object:
        requested.append(url)
        room = "lobby" if "/lobby?" in url else "technocore"
        return {
            "room": room,
            "count": 0,
            "first_seq": None,
            "last_seq": 0,
            "messages": [],
        }

    monkeypatch.setattr(collector, "fetch_json", fake_fetch)
    snapshot = collector.collect_snapshot(
        base_url="https://technocore.chat",
        room_limit=5,
        message_limit=50,
        generated_at="2026-08-24T20:02:00Z",
        room_names=["lobby", "technocore"],
    )

    assert requested == [
        "https://technocore.chat/r/lobby?format=json&limit=50",
        "https://technocore.chat/r/technocore?format=json&limit=50",
    ]
    assert snapshot["collection"]["discovery"] == "explicit room allowlist"
    assert snapshot["collection"]["directory_total_rooms"] is None


def test_collect_snapshot_rejects_returned_room_mismatch(monkeypatch) -> None:
    def fake_fetch(url: str, *, timeout: float, max_bytes: int) -> object:
        return {
            "room": "technocore",
            "count": 0,
            "first_seq": None,
            "last_seq": 0,
            "messages": [],
        }

    monkeypatch.setattr(collector, "fetch_json", fake_fetch)
    with pytest.raises(SchemaError, match="requested room"):
        collector.collect_snapshot(
            base_url="https://technocore.chat",
            room_limit=5,
            message_limit=50,
            generated_at="2026-08-24T20:02:00Z",
            room_names=["lobby"],
        )


@pytest.mark.parametrize("rooms", [["bad|room"], ["lobby", "lobby"], [], [True]])
def test_collect_snapshot_rejects_invalid_explicit_rooms_before_network(
    monkeypatch, rooms: list[object]
) -> None:
    called = False

    def fake_fetch(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(collector, "fetch_json", fake_fetch)
    with pytest.raises(ValueError, match="explicit rooms"):
        collector.collect_snapshot(
            base_url="https://technocore.chat",
            room_limit=5,
            message_limit=50,
            generated_at="2026-08-24T20:02:00Z",
            room_names=rooms,
        )
    assert called is False


def test_collect_snapshot_refuses_non_https_origin_before_network(monkeypatch) -> None:
    called = False

    def fake_fetch(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(collector, "fetch_json", fake_fetch)

    with pytest.raises(ValueError, match="HTTPS origin"):
        collector.collect_snapshot(
            base_url="http://technocore.chat",
            room_limit=5,
            message_limit=50,
            generated_at="2026-08-24T20:02:00Z",
        )
    assert called is False


@pytest.mark.parametrize("room_limit,message_limit", [(0, 50), (201, 50), (5, 0), (5, 201)])
def test_collect_snapshot_enforces_server_limits(room_limit: int, message_limit: int) -> None:
    with pytest.raises(ValueError):
        collector.collect_snapshot(
            base_url="https://technocore.chat",
            room_limit=room_limit,
            message_limit=message_limit,
            generated_at="2026-08-24T20:02:00Z",
        )


@pytest.mark.parametrize(
    "timeout,max_bytes",
    [
        (True, 1024),
        (float("nan"), 1024),
        (float("inf"), 1024),
        (1.0, True),
    ],
)
def test_collect_snapshot_rejects_ambiguous_numeric_limits(
    timeout: object,
    max_bytes: object,
) -> None:
    with pytest.raises(ValueError, match="timeout and max_bytes"):
        collector.collect_snapshot(
            base_url="https://technocore.chat",
            room_limit=5,
            message_limit=50,
            generated_at="2026-08-24T20:02:00Z",
            timeout=timeout,
            max_bytes=max_bytes,
            room_names=["lobby"],
        )


@pytest.mark.parametrize("attempts,delay", [(True, 1.0), (1, True), (1, float("nan"))])
def test_collect_with_retries_rejects_ambiguous_numeric_policy(
    attempts: object,
    delay: object,
) -> None:
    with pytest.raises(ValueError, match="invalid retry policy"):
        collector.collect_with_retries(attempts=attempts, delay=delay)


def test_write_snapshot_is_atomic_and_canonical(tmp_path: Path) -> None:
    destination = tmp_path / "data" / "snapshot.json"
    payload = {"schema_version": 1, "rooms": [], "projects": []}

    collector.write_snapshot(destination, payload)

    assert json.loads(destination.read_text()) == payload
    assert destination.read_text().endswith("\n")
    assert not list(destination.parent.glob("*.tmp"))
