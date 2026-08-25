from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

ROOM_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,47}\Z")
UTC_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")
MESSAGE_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{6})?Z\Z")
GITHUB_PATH_RE = re.compile(r"/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
BASE58_INDEX = {
    character: index
    for index, character in enumerate("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")
}
ED25519_MULTICODEC = b"\xed\x01"
SIGNAL_PREFIX = "SIGNAL/1 "
SIGNAL_KEYS = {"project", "summary", "repo", "stage", "seeking"}
SIGNAL_STAGES = {"planning", "building", "usable", "maintained", "complete", "help-wanted"}
COORDINATION_RE = re.compile(
    r"\b(?:proposal|status|handoff|owner|seeking|review requested|help wanted|next step)\b",
    re.IGNORECASE,
)
ROOM_PAYLOAD_KEYS = {"room", "count", "first_seq", "last_seq", "messages"}
UNSIGNED_MESSAGE_KEYS = {"seq", "ts", "from", "text"}
SIGNED_MESSAGE_KEYS = UNSIGNED_MESSAGE_KEYS | {"nonce"}
MAX_NONCE = 9_999_999_999_999_999_999
MAX_SAFE_INTEGER = 9_007_199_254_740_991


class SchemaError(ValueError):
    """Raised when a remote container does not match the required schema."""


def _valid_room(value: object) -> bool:
    return type(value) is str and ROOM_RE.fullmatch(value) is not None


def is_ed25519_did(value: object) -> bool:
    """Validate a did:key multibase payload as one exact Ed25519 public key."""
    if type(value) is not str or not value.startswith("did:key:z"):
        return False
    encoded = value.removeprefix("did:key:z")
    if len(encoded) != 47:
        return False
    number = 0
    for character in encoded:
        digit = BASE58_INDEX.get(character)
        if digit is None:
            return False
        number = number * 58 + digit
    leading_zeroes = len(encoded) - len(encoded.lstrip("1"))
    decoded_size = (number.bit_length() + 7) // 8
    decoded = b"\x00" * leading_zeroes
    if decoded_size:
        decoded += number.to_bytes(decoded_size, "big")
    return len(decoded) == 34 and decoded.startswith(ED25519_MULTICODEC)


def _valid_timestamp(value: object) -> bool:
    if type(value) is not str or MESSAGE_TIMESTAMP_RE.fullmatch(value) is None:
        return False
    try:
        datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError:
        return False
    return True


def _validate_record(value: object) -> None:
    if type(value) is not dict:
        raise SchemaError("room message must be an object")
    sender = value.get("from")
    signed = is_ed25519_did(sender)
    expected_keys = SIGNED_MESSAGE_KEYS if signed else UNSIGNED_MESSAGE_KEYS
    if set(value) != expected_keys:
        raise SchemaError("room message has invalid fields")
    if type(sender) is not str or (not signed and ROOM_RE.fullmatch(sender) is None):
        raise SchemaError("room message has invalid sender")
    if type(value["seq"]) is not int or value["seq"] <= 0 or value["seq"] > MAX_SAFE_INTEGER:
        raise SchemaError("room message has invalid sequence")
    if not _valid_timestamp(value["ts"]):
        raise SchemaError("room message has invalid timestamp")
    text = value["text"]
    if (
        type(text) is not str
        or not 1 <= len(text) <= 4096
        or any(unicodedata.category(character) in {"Cc", "Cf"} for character in text)
    ):
        raise SchemaError("room message has invalid text")
    if signed and (type(value["nonce"]) is not int or not 1 <= value["nonce"] <= MAX_NONCE):
        raise SchemaError("room message has invalid nonce")


def _valid_record(value: object) -> bool:
    try:
        _validate_record(value)
    except (KeyError, SchemaError):
        return False
    return True


def _round_ratio(numerator: int, denominator: int) -> float:
    if not denominator:
        return 0.0
    quotient, remainder = divmod(numerator * 10_000, denominator)
    if remainder * 2 > denominator or (remainder * 2 == denominator and quotient % 2 == 1):
        quotient += 1
    return quotient / 10_000


def analyze_room(
    payload: object,
    *,
    expected_room: str | None = None,
    message_limit: int = 200,
) -> dict[str, object]:
    """Reduce an untrusted room response to body-free quality metrics."""
    if type(payload) is not dict:
        raise SchemaError("room payload must be an object")
    if set(payload) != ROOM_PAYLOAD_KEYS:
        raise SchemaError("room payload has invalid fields")
    if type(message_limit) is not int or not 1 <= message_limit <= 200:
        raise SchemaError("invalid message limit")
    room = payload.get("room")
    if not _valid_room(room):
        raise SchemaError("room payload has an invalid room")
    if expected_room is not None and room != expected_room:
        raise SchemaError("room payload does not match requested room")
    messages = payload.get("messages")
    if type(messages) is not list:
        raise SchemaError("room messages must be a list")
    if len(messages) > message_limit:
        raise SchemaError("room messages exceed requested message limit")
    if type(payload["count"]) is not int or payload["count"] != len(messages):
        raise SchemaError("room count does not match messages")
    for record in messages:
        try:
            _validate_record(record)
        except KeyError as error:
            raise SchemaError("room message is missing a required field") from error
    seqs = [record["seq"] for record in messages]
    if any(current != previous + 1 for previous, current in zip(seqs, seqs[1:], strict=False)):
        raise SchemaError("room message sequences are not contiguous oldest-first")
    expected_first = seqs[0] if seqs else None
    expected_last = seqs[-1] if seqs else 0
    if payload["first_seq"] != expected_first or payload["last_seq"] != expected_last:
        raise SchemaError("room sequence bounds are inconsistent")
    if payload["first_seq"] is not None and type(payload["first_seq"]) is not int:
        raise SchemaError("room first_seq must be an exact integer or null")
    if type(payload["last_seq"]) is not int:
        raise SchemaError("room last_seq must be an exact integer")

    valid = messages
    senders = Counter(record["from"] for record in valid)
    signed_senders = [record["from"] for record in valid if is_ed25519_did(record["from"])]
    text_counts = Counter(record["text"] for record in valid)
    duplicate_extras = sum(count - 1 for count in text_counts.values() if count > 1)

    return {
        "room": room,
        "message_count": len(valid),
        "rejected_record_count": 0,
        "signed_message_count": len(signed_senders),
        "anonymous_message_count": len(valid) - len(signed_senders),
        "unique_sender_count": len(senders),
        "unique_signed_sender_count": len(set(signed_senders)),
        "top_sender_share": _round_ratio(max(senders.values(), default=0), len(valid)),
        "duplicate_extra_count": duplicate_extras,
        "duplicate_rate": _round_ratio(duplicate_extras, len(valid)),
        "question_count": sum(record["text"].rstrip().endswith("?") for record in valid),
        "coordination_signal_count": sum(
            COORDINATION_RE.search(record["text"]) is not None for record in valid
        ),
        "first_seq": min(seqs) if seqs else None,
        "last_seq": max(seqs) if seqs else 0,
    }


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _safe_text(value: object, *, maximum: int) -> bool:
    if type(value) is not str or not value or value != value.strip() or len(value) > maximum:
        return False
    return not any(unicodedata.category(character) in {"Cc", "Cf"} for character in value)


def _safe_github_repo(value: object) -> bool:
    if type(value) is not str or len(value) > 200:
        return False
    parts = urlsplit(value)
    return (
        parts.scheme == "https"
        and parts.netloc == "github.com"
        and not parts.query
        and not parts.fragment
        and GITHUB_PATH_RE.fullmatch(parts.path) is not None
    )


def _canonical_source_origin(value: object) -> str:
    if type(value) is not str:
        raise SchemaError("source origin must be a string")
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError as error:
        raise SchemaError("source origin is invalid") from error
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.path not in {"", "/"}
        or parts.query
        or parts.fragment
    ):
        raise SchemaError("source origin must be a bare HTTPS origin")
    suffix = f":{port}" if port is not None and port != 443 else ""
    return f"https://{parts.hostname.lower()}{suffix}"


def parse_signal_card(
    room: str,
    record: object,
    *,
    source_origin: str = "https://technocore.chat",
) -> dict[str, object] | None:
    """Parse an explicitly opted-in signed project card without following its URL."""
    if not _valid_room(room) or not _valid_record(record):
        return None
    origin = _canonical_source_origin(source_origin)
    sender = record["from"]
    text = record["text"]
    if not is_ed25519_did(sender) or not text.startswith(SIGNAL_PREFIX):
        return None

    try:
        body = json.loads(text[len(SIGNAL_PREFIX) :], object_pairs_hook=_reject_duplicate_keys)
    except (json.JSONDecodeError, ValueError):
        return None
    if type(body) is not dict or set(body) != SIGNAL_KEYS:
        return None
    if not _safe_text(body["project"], maximum=80):
        return None
    if not _safe_text(body["summary"], maximum=280):
        return None
    if not _safe_text(body["seeking"], maximum=120):
        return None
    if type(body["stage"]) is not str or body["stage"] not in SIGNAL_STAGES:
        return None
    if not _safe_github_repo(body["repo"]):
        return None

    seq = record["seq"]
    return {
        "project": body["project"],
        "summary": body["summary"],
        "repo": body["repo"],
        "stage": body["stage"],
        "seeking": body["seeking"],
        "server_reported_did": sender,
        "room": room,
        "seq": seq,
        "source_url": f"{origin}/humans#r/{room}/{seq}",
        "authorship": "server-reported; inbound signature unavailable",
    }


def _validate_generated_at(value: str) -> None:
    if type(value) is not str or UTC_TIMESTAMP_RE.fullmatch(value) is None:
        raise ValueError("generated_at must be a canonical UTC timestamp")
    datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")


def build_snapshot(
    room_payloads: object,
    *,
    generated_at: str,
    expected_rooms: list[str] | None = None,
    message_limit: int = 200,
    source_origin: str = "https://technocore.chat",
) -> dict[str, object]:
    """Build a deterministic public snapshot with no raw room-message bodies."""
    _validate_generated_at(generated_at)
    origin = _canonical_source_origin(source_origin)
    if type(room_payloads) is not list:
        raise SchemaError("room_payloads must be a list")
    if expected_rooms is not None and (
        type(expected_rooms) is not list or len(expected_rooms) != len(room_payloads)
    ):
        raise SchemaError("expected_rooms must match room_payloads")

    analyses: list[dict[str, object]] = []
    cards_by_claim: dict[tuple[str, str, str], dict[str, object]] = {}
    for index, payload in enumerate(room_payloads):
        expected_room = expected_rooms[index] if expected_rooms is not None else None
        analysis = analyze_room(
            payload,
            expected_room=expected_room,
            message_limit=message_limit,
        )
        analyses.append(analysis)
        room = analysis["room"]
        for record in payload["messages"]:
            card = parse_signal_card(room, record, source_origin=origin)
            if card is None:
                continue
            claim = (card["server_reported_did"], card["repo"], card["room"])
            previous = cards_by_claim.get(claim)
            if previous is None or card["seq"] > previous["seq"]:
                cards_by_claim[claim] = card

    analyses.sort(key=lambda item: (-item["message_count"], item["room"]))
    projects = sorted(
        cards_by_claim.values(),
        key=lambda item: (
            item["project"].casefold(),
            item["repo"],
            item["server_reported_did"],
            item["room"],
        ),
    )
    return {
        "schema_version": 1,
        "generated_at": generated_at,
        "source": origin,
        "trust": {
            "room_content_stored": False,
            "links_followed": False,
            "sender_identity": "server-reported unless an external signature receipt is linked",
            "eligibility": "No FLOP airdrop eligibility or allocation is calculated or implied.",
        },
        "totals": {
            "rooms_analyzed": len(analyses),
            "messages_analyzed": sum(item["message_count"] for item in analyses),
            "rejected_records": sum(item["rejected_record_count"] for item in analyses),
            "signed_messages": sum(item["signed_message_count"] for item in analyses),
            "anonymous_messages": sum(item["anonymous_message_count"] for item in analyses),
            "duplicate_extras": sum(item["duplicate_extra_count"] for item in analyses),
            "coordination_signals": sum(item["coordination_signal_count"] for item in analyses),
            "project_cards": len(projects),
        },
        "rooms": analyses,
        "projects": projects,
    }
