from __future__ import annotations

import json

import pytest

from technocore_signal.core import (
    SchemaError,
    analyze_room,
    build_snapshot,
    is_ed25519_did,
    parse_signal_card,
)

BASE58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def ed25519_did(public_byte: int) -> str:
    payload = b"\xed\x01" + bytes([public_byte]) * 32
    number = int.from_bytes(payload, "big")
    encoded = ""
    while number:
        number, remainder = divmod(number, 58)
        encoded = BASE58[remainder] + encoded
    return f"did:key:z{encoded}"


DID_A = ed25519_did(1)
DID_B = ed25519_did(2)


def test_ed25519_did_validation_checks_multicodec_and_exact_key_bytes() -> None:
    assert is_ed25519_did(DID_A) is True
    assert is_ed25519_did(DID_B) is True
    assert is_ed25519_did("did:key:z6Mkshort") is False
    assert is_ed25519_did("did:key:z6Mk00000000000000000000000000000000000000000000") is False
    assert is_ed25519_did("did:key:z" + "1" * 34) is False


def test_duplicate_rate_uses_deterministic_four_decimal_half_even_rounding() -> None:
    messages = [
        message(index, f"agent-{index}", "same" if index <= 2 else str(index))
        for index in range(1, 33)
    ]

    result = analyze_room(room_payload(messages))

    assert result["duplicate_rate"] == 0.0312


def message(seq: int, sender: str, text: str, *, nonce: int | None = None) -> dict:
    record = {
        "seq": seq,
        "ts": f"2026-08-24T12:{seq:02d}:00Z",
        "from": sender,
        "text": text,
    }
    if nonce is not None:
        record["nonce"] = nonce
    return record


def room_payload(messages: list[dict], room: str = "lobby") -> dict:
    return {
        "room": room,
        "count": len(messages),
        "first_seq": messages[0]["seq"] if messages else None,
        "last_seq": messages[-1]["seq"] if messages else 0,
        "messages": messages,
    }


def test_analyze_room_reports_quality_not_just_volume() -> None:
    payload = room_payload(
        [
            message(1, DID_A, "Who can review the parser?", nonce=11),
            message(2, DID_A, "Who can review the parser?", nonce=12),
            message(3, "alice", "status: tests pass"),
            message(4, DID_B, "proposal: add bounded retries", nonce=21),
            message(5, DID_B, "handoff: review requested", nonce=22),
        ]
    )

    result = analyze_room(payload)

    assert result == {
        "room": "lobby",
        "message_count": 5,
        "rejected_record_count": 0,
        "signed_message_count": 4,
        "anonymous_message_count": 1,
        "unique_sender_count": 3,
        "unique_signed_sender_count": 2,
        "top_sender_share": 0.4,
        "duplicate_extra_count": 1,
        "duplicate_rate": 0.2,
        "question_count": 2,
        "coordination_signal_count": 3,
        "first_seq": 1,
        "last_seq": 5,
    }
    assert "Who can review" not in json.dumps(result)


def test_analyze_room_fails_closed_on_malformed_records_without_reflecting_content() -> None:
    payload = room_payload(
        [
            message(1, DID_A, "valid", nonce=1),
            {"seq": True, "ts": "now", "from": ["attacker"], "text": {"nested": "payload"}},
            {"seq": 3, "ts": "now", "from": "alice"},
        ]
    )

    with pytest.raises(SchemaError) as error:
        analyze_room(payload)

    assert "attacker" not in str(error.value)
    assert "nested" not in str(error.value)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.update(count=99),
        lambda payload: payload.update(first_seq=2),
        lambda payload: payload.update(last_seq=1),
        lambda payload: payload["messages"][0].update(seq=0),
        lambda payload: payload["messages"][0].update(ts="not-a-time"),
        lambda payload: payload["messages"][0].update(nonce=-1),
        lambda payload: payload["messages"][1].update(seq=1),
        lambda payload: payload["messages"][0].update(extra="unknown"),
    ],
)
def test_analyze_room_fails_closed_on_inconsistent_envelope(mutate) -> None:
    payload = room_payload(
        [
            message(1, DID_A, "one", nonce=1),
            message(2, DID_B, "two", nonce=2),
        ]
    )
    mutate(payload)

    with pytest.raises(SchemaError):
        analyze_room(payload, expected_room="lobby", message_limit=2)


def test_analyze_room_binds_requested_room_and_limit() -> None:
    payload = room_payload([message(1, "alice", "one")], room="technocore")

    with pytest.raises(SchemaError, match="requested room"):
        analyze_room(payload, expected_room="lobby", message_limit=1)
    with pytest.raises(SchemaError, match="message limit"):
        analyze_room(payload, expected_room="technocore", message_limit=0)


def test_build_snapshot_emits_browser_compatible_empty_room_bounds() -> None:
    snapshot = build_snapshot(
        [room_payload([])],
        generated_at="2026-08-24T20:00:00Z",
        expected_rooms=["lobby"],
    )

    assert snapshot["rooms"][0]["first_seq"] is None
    assert snapshot["rooms"][0]["last_seq"] == 0


def test_analyze_room_rejects_sequence_above_javascript_safe_integer() -> None:
    payload = room_payload([message(1, DID_A, "one", nonce=1)])
    payload["messages"][0]["seq"] = 9_007_199_254_740_992
    payload["first_seq"] = 9_007_199_254_740_992
    payload["last_seq"] = 9_007_199_254_740_992

    with pytest.raises(SchemaError, match="sequence"):
        analyze_room(payload)


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"room": "lobby", "messages": {}},
        {"room": "bad|room", "messages": []},
        {"room": "lobby", "messages": [], "first_seq": True},
    ],
)
def test_analyze_room_fails_closed_on_invalid_container(payload: object) -> None:
    with pytest.raises(SchemaError):
        analyze_room(payload)


def test_parse_signal_card_accepts_strict_opt_in_signed_project() -> None:
    body = {
        "project": "Safe Relay",
        "summary": "Bounded, testable coordination across agent runtimes.",
        "repo": "https://github.com/acme/safe-relay",
        "stage": "building",
        "seeking": "Protocol reviewers",
    }
    record = message(42, DID_A, f"SIGNAL/1 {json.dumps(body)}", nonce=99)

    card = parse_signal_card("technocore", record)

    assert card == {
        "project": "Safe Relay",
        "summary": "Bounded, testable coordination across agent runtimes.",
        "repo": "https://github.com/acme/safe-relay",
        "stage": "building",
        "seeking": "Protocol reviewers",
        "server_reported_did": DID_A,
        "room": "technocore",
        "seq": 42,
        "source_url": "https://technocore.chat/humans#r/technocore/42",
        "authorship": "server-reported; inbound signature unavailable",
    }
    assert "SIGNAL/1" not in json.dumps(card)


@pytest.mark.parametrize(
    "sender,text",
    [
        ("alice", 'SIGNAL/1 {"project":"x"}'),
        ("did:key:z6Mkshort", 'SIGNAL/1 {"project":"x"}'),
        ("did:key:z6Mk00000000000000000000000000000000000000000000", 'SIGNAL/1 {"project":"x"}'),
        (DID_A, "not a card"),
        (DID_A, 'SIGNAL/1 {"project":"x","project":"y"}'),
        (
            DID_A,
            'SIGNAL/1 {"project":"x","summary":"x","repo":"javascript:alert(1)",'
            '"stage":"building","seeking":"x"}',
        ),
        (
            DID_A,
            'SIGNAL/1 {"project":"x","summary":"x","repo":"https://github.com/a/b?x=1",'
            '"stage":"building","seeking":"x"}',
        ),
        (
            DID_A,
            'SIGNAL/1 {"project":"x","summary":"x","repo":"https://github.com/a/b",'
            '"stage":"invented","seeking":"x"}',
        ),
        (
            DID_A,
            'SIGNAL/1 {"project":"x","summary":"x","repo":"https://github.com/a/b",'
            '"stage":"building","seeking":"x","html":"<script>"}',
        ),
    ],
)
def test_parse_signal_card_rejects_unsafe_or_ambiguous_input(sender: str, text: str) -> None:
    assert parse_signal_card("lobby", message(7, sender, text, nonce=1)) is None


def test_build_snapshot_contains_aggregates_and_cards_but_no_raw_messages() -> None:
    card_text = json.dumps(
        {
            "project": "Signal",
            "summary": "Find useful work without rewarding message volume.",
            "repo": "https://github.com/snoodoodles/technocore-signal",
            "stage": "building",
            "seeking": "Early testers",
        }
    )
    rooms = [
        room_payload(
            [
                message(1, DID_A, f"SIGNAL/1 {card_text}", nonce=1),
                message(2, DID_A, "ordinary untrusted room body", nonce=2),
            ],
            "lobby",
        )
    ]

    snapshot = build_snapshot(rooms, generated_at="2026-08-24T20:00:00Z")
    serialized = json.dumps(snapshot)

    assert snapshot["schema_version"] == 1
    assert snapshot["totals"]["rooms_analyzed"] == 1
    assert snapshot["totals"]["messages_analyzed"] == 2
    assert snapshot["totals"]["project_cards"] == 1
    assert snapshot["projects"][0]["project"] == "Signal"
    assert "ordinary untrusted room body" not in serialized
    assert "SIGNAL/1" not in serialized
    assert "messages" not in snapshot["rooms"][0]


def test_build_snapshot_retains_conflicting_project_claimants() -> None:
    card = {
        "project": "Shared Name",
        "summary": "A claimed project.",
        "repo": "https://github.com/acme/shared",
        "stage": "building",
        "seeking": "Reviewers",
    }
    payload = room_payload(
        [
            message(1, DID_A, f"SIGNAL/1 {json.dumps(card)}", nonce=1),
            message(2, DID_B, f"SIGNAL/1 {json.dumps(card)}", nonce=2),
        ]
    )

    snapshot = build_snapshot([payload], generated_at="2026-08-24T20:00:00Z")

    assert len(snapshot["projects"]) == 2
    assert {project["server_reported_did"] for project in snapshot["projects"]} == {
        DID_A,
        DID_B,
    }


def test_build_snapshot_uses_collection_origin_for_sources() -> None:
    card = {
        "project": "Self-hosted",
        "summary": "A test instance project.",
        "repo": "https://github.com/acme/self-hosted",
        "stage": "building",
        "seeking": "Reviewers",
    }
    payload = room_payload(
        [message(1, DID_A, f"SIGNAL/1 {json.dumps(card)}", nonce=1)],
        room="test-room",
    )

    snapshot = build_snapshot(
        [payload],
        generated_at="2026-08-24T20:00:00Z",
        source_origin="https://chat.example:444",
    )

    assert snapshot["source"] == "https://chat.example:444"
    assert snapshot["projects"][0]["source_url"] == (
        "https://chat.example:444/humans#r/test-room/1"
    )


def test_build_snapshot_requires_canonical_utc_timestamp() -> None:
    with pytest.raises(ValueError):
        build_snapshot([], generated_at="August 24")
