from __future__ import annotations

import argparse
import json
import math
import os
import tempfile
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .core import MAX_SAFE_INTEGER, ROOM_RE, SchemaError, build_snapshot

DEFAULT_BASE_URL = "https://technocore.chat"
DEFAULT_MAX_BYTES = 1 << 20
TRANSIENT_HTTP_CODES = {429, 502, 503, 504}
DIRECTORY_KEYS = {
    "rooms",
    "total",
    "capacity",
    "bytes",
    "bytes_capacity",
    "engagement",
    "notes",
    "untrusted",
}
DIRECTORY_ROOM_KEYS = {
    "room",
    "last_seq",
    "bytes",
    "idle_seconds",
    "topic",
    "window",
    "zero_response_share",
    "nick_diversity",
}


def _origin_identity(url: str) -> tuple[str, str, int] | None:
    try:
        parts = urlsplit(url)
        port = parts.port or (443 if parts.scheme.lower() == "https" else 80)
    except ValueError:
        return None
    if parts.username or parts.password or not parts.hostname:
        return None
    return parts.scheme.lower(), parts.hostname.lower(), port


class SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject a redirect target before urllib makes the redirected request."""

    max_redirections = 3

    def __init__(self, origin: str) -> None:
        super().__init__()
        self.origin = _origin_identity(origin)

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if self.origin is None or _origin_identity(newurl) != self.origin:
            raise urllib.error.HTTPError(
                req.full_url,
                code,
                "redirect refused before follow",
                headers,
                fp,
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _validated_origin(base_url: str) -> str:
    if type(base_url) is not str:
        raise ValueError("base URL must be a string")
    parts = urlsplit(base_url)
    if (
        parts.scheme != "https"
        or not parts.netloc
        or parts.path not in {"", "/"}
        or parts.query
        or parts.fragment
        or parts.username
        or parts.password
    ):
        raise ValueError("base URL must be a bare HTTPS origin")
    return f"https://{parts.netloc}"


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SchemaError("duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise SchemaError(f"non-standard JSON constant: {value}")


def decode_json(raw: bytes) -> object:
    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SchemaError("response is not valid UTF-8 JSON") from error


def fetch_json(url: str, *, timeout: float, max_bytes: int) -> object:
    """Fetch one bounded JSON object without allowing a cross-origin redirect."""
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "technocore-signal/0.1 (+https://github.com/snoodoodles/technocore-signal)",
        },
    )
    opener = urllib.request.build_opener(SameOriginRedirectHandler(url))
    with opener.open(request, timeout=timeout) as response:
        requested = urlsplit(url)
        final = urlsplit(response.geturl())
        if (requested.scheme, requested.netloc) != (final.scheme, final.netloc):
            raise ValueError("cross-origin redirect refused")
        declared = response.headers.get("Content-Length")
        if declared is not None:
            try:
                if int(declared) > max_bytes:
                    raise SchemaError("response exceeds byte limit")
            except ValueError as error:
                raise SchemaError("invalid Content-Length") from error
        raw = response.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise SchemaError("response exceeds byte limit")
    value = decode_json(raw)
    if type(value) is not dict:
        raise SchemaError("response JSON root must be an object")
    return value


def extract_room_names(payload: object, *, maximum: int) -> list[str]:
    """Validate one complete directory page and retain only room identifiers."""
    if type(maximum) is not int or not 1 <= maximum <= 200:
        raise ValueError("maximum must be between 1 and 200")
    if type(payload) is not dict or set(payload) != DIRECTORY_KEYS:
        raise SchemaError("rooms payload has invalid fields")
    rooms = payload.get("rooms")
    total = payload.get("total")
    if type(rooms) is not list or type(total) is not int or total < 0:
        raise SchemaError("rooms payload has invalid containers")
    for key in ("capacity", "bytes", "bytes_capacity"):
        if type(payload[key]) is not int or payload[key] < 0:
            raise SchemaError(f"rooms payload has invalid {key}")
    if total > payload["capacity"] or payload["bytes"] > payload["bytes_capacity"]:
        raise SchemaError("rooms payload exceeds declared capacity")
    engagement = payload["engagement"]
    if type(engagement) is not dict or set(engagement) != {
        "window_cap",
        "windowed_messages",
        "zero_response_share",
        "nick_diversity",
        "windowed_note_to_message_ratio",
    }:
        raise SchemaError("rooms payload has invalid engagement")
    if (
        type(engagement["window_cap"]) is not int
        or not 1 <= engagement["window_cap"] <= 200
        or type(engagement["windowed_messages"]) is not int
        or engagement["windowed_messages"] < 0
        or any(
            type(engagement[key]) not in {int, float}
            or not math.isfinite(engagement[key])
            or not 0 <= engagement[key] <= 1
            for key in ("zero_response_share", "nick_diversity")
        )
        or type(engagement["windowed_note_to_message_ratio"]) not in {int, float}
        or not math.isfinite(engagement["windowed_note_to_message_ratio"])
        or engagement["windowed_note_to_message_ratio"] < 0
    ):
        raise SchemaError("rooms payload has invalid engagement values")
    notes = payload["notes"]
    notes_keys = {"total", "bytes", "capacity"}
    if type(notes) is not dict or not notes_keys <= set(notes) <= notes_keys | {
        "capacity_per_namespace"
    }:
        raise SchemaError("rooms payload has invalid notes")
    if "capacity_per_namespace" in notes and (
        type(notes["capacity_per_namespace"]) is not int or notes["capacity_per_namespace"] <= 0
    ):
        raise SchemaError("rooms payload has invalid capacity_per_namespace")
    if any(type(notes[key]) is not int or notes[key] < 0 for key in notes):
        raise SchemaError("rooms payload has invalid note values")
    if notes["total"] > notes["capacity"]:
        raise SchemaError("rooms payload exceeds note capacity")
    untrusted = payload["untrusted"]
    if (
        type(untrusted) is not dict
        or set(untrusted) != {"fields", "note"}
        or untrusted["fields"] != ["room", "topic"]
        or type(untrusted["note"]) is not str
        or not 1 <= len(untrusted["note"]) <= 2048
    ):
        raise SchemaError("rooms payload has invalid untrusted marker")
    if len(rooms) != min(total, maximum):
        raise SchemaError("directory count does not match requested page")

    names: list[str] = []
    seen: set[str] = set()
    for entry in rooms:
        if type(entry) is not dict or set(entry) != DIRECTORY_ROOM_KEYS:
            raise SchemaError("directory room has invalid fields")
        room = entry["room"]
        topic = entry["topic"]
        if (
            type(room) is not str
            or ROOM_RE.fullmatch(room) is None
            or room in seen
            or (topic is not None and (type(topic) is not str or len(topic) > 2048))
            or any(
                type(entry[key]) is not int or entry[key] < 0
                for key in ("last_seq", "bytes", "idle_seconds")
            )
            or entry["last_seq"] > MAX_SAFE_INTEGER
            or type(entry["window"]) is not int
            or not 0 <= entry["window"] <= engagement["window_cap"]
            or any(
                type(entry[key]) not in {int, float}
                or not math.isfinite(entry[key])
                or not 0 <= entry[key] <= 1
                for key in ("zero_response_share", "nick_diversity")
            )
        ):
            raise SchemaError("directory room has invalid values")
        seen.add(room)
        names.append(room)
    return names


def collect_snapshot(
    *,
    base_url: str,
    room_limit: int,
    message_limit: int,
    generated_at: str,
    timeout: float = 30.0,
    max_bytes: int = DEFAULT_MAX_BYTES,
    room_names: list[str] | None = None,
) -> dict[str, object]:
    """Collect a body-free snapshot from the newest valid public rooms."""
    origin = _validated_origin(base_url)
    if type(room_limit) is not int or not 1 <= room_limit <= 200:
        raise ValueError("room_limit must be an integer between 1 and 200")
    if type(message_limit) is not int or not 1 <= message_limit <= 200:
        raise ValueError("message_limit must be an integer between 1 and 200")
    if (
        type(timeout) not in {int, float}
        or not math.isfinite(timeout)
        or timeout <= 0
        or type(max_bytes) is not int
        or max_bytes < 1
    ):
        raise ValueError("timeout and max_bytes must be positive")

    if room_names is not None:
        if (
            type(room_names) is not list
            or not room_names
            or len(room_names) > room_limit
            or any(type(room) is not str or ROOM_RE.fullmatch(room) is None for room in room_names)
            or len(set(room_names)) != len(room_names)
        ):
            raise ValueError("explicit rooms must be a non-empty unique list of valid names")
        selected_rooms = room_names
        directory_total = None
        discovery = "explicit room allowlist"
    else:
        directory = fetch_json(
            f"{origin}/rooms?format=json&limit={room_limit}",
            timeout=timeout,
            max_bytes=max_bytes,
        )
        selected_rooms = extract_room_names(directory, maximum=room_limit)
        directory_total = directory["total"]
        discovery = "newest rooms directory"
    payloads = [
        fetch_json(
            f"{origin}/r/{room}?format=json&limit={message_limit}",
            timeout=timeout,
            max_bytes=max_bytes,
        )
        for room in selected_rooms
    ]
    snapshot = build_snapshot(
        payloads,
        generated_at=generated_at,
        expected_rooms=selected_rooms,
        message_limit=message_limit,
        source_origin=origin,
    )
    snapshot["collection"] = {
        "directory_total_rooms": directory_total,
        "discovery": discovery,
        "room_limit": room_limit,
        "message_limit": message_limit,
        "response_byte_limit": max_bytes,
    }
    return snapshot


def collect_with_retries(
    *,
    attempts: int = 3,
    delay: float = 2.0,
    **kwargs: Any,
) -> dict[str, object]:
    if (
        type(attempts) is not int
        or attempts < 1
        or type(delay) not in {int, float}
        or not math.isfinite(delay)
        or delay < 0
    ):
        raise ValueError("invalid retry policy")
    for attempt in range(attempts):
        try:
            return collect_snapshot(**kwargs)
        except urllib.error.HTTPError as error:
            if error.code not in TRANSIENT_HTTP_CODES or attempt + 1 == attempts:
                raise
        except (TimeoutError, urllib.error.URLError):
            if attempt + 1 == attempts:
                raise
        time.sleep(delay * (2**attempt))
    raise RuntimeError("retry loop exhausted")


def write_snapshot(destination: Path, payload: object) -> None:
    """Atomically replace a snapshot only after the complete JSON is serialized."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a body-free Technocore activity and project snapshot."
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--room-limit", type=int, default=5)
    parser.add_argument("--message-limit", type=int, default=200)
    parser.add_argument(
        "--rooms",
        nargs="+",
        help="Collect these validated rooms instead of querying the unstable /rooms directory.",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--output", type=Path, default=Path("docs/data/snapshot.json"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        snapshot = collect_with_retries(
            attempts=args.attempts,
            base_url=args.base_url,
            room_limit=args.room_limit,
            message_limit=args.message_limit,
            generated_at=_utc_now(),
            timeout=args.timeout,
            room_names=args.rooms,
        )
        write_snapshot(args.output, snapshot)
    except (OSError, ValueError, SchemaError) as error:
        print(f"error: {error}", file=os.sys.stderr)
        return 2
    totals = snapshot["totals"]
    print(
        f"wrote {args.output}: {totals['rooms_analyzed']} rooms, "
        f"{totals['messages_analyzed']} messages, {totals['project_cards']} project cards"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
