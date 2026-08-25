from __future__ import annotations

import json
import subprocess
from pathlib import Path

from technocore_signal.core import build_snapshot

ROOT = Path(__file__).parents[1]


def test_frontend_semantic_validator_rejects_inconsistent_public_data() -> None:
    result = subprocess.run(
        ["node", str(ROOT / "tests" / "frontend_validation.cjs")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "frontend semantic validation passed"


def test_empty_backend_snapshot_passes_public_artifact_validator(tmp_path: Path) -> None:
    snapshot = build_snapshot(
        [
            {
                "room": "empty",
                "count": 0,
                "first_seq": None,
                "last_seq": 0,
                "messages": [],
            }
        ],
        generated_at="2026-08-25T05:30:24Z",
        expected_rooms=["empty"],
    )
    snapshot["collection"] = {
        "directory_total_rooms": None,
        "discovery": "explicit room allowlist",
        "room_limit": 1,
        "message_limit": 200,
        "response_byte_limit": 1_048_576,
    }
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(json.dumps(snapshot))

    result = subprocess.run(
        [
            "node",
            str(ROOT / "scripts" / "validate_public_data.cjs"),
            str(snapshot_path),
            str(ROOT / "docs" / "data" / "proofs.json"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "public data semantic validation passed"
