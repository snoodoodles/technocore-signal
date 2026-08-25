from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).parents[1]
DOCS = ROOT / "docs"


def test_page_has_security_policy_and_accessible_structure() -> None:
    html = (DOCS / "index.html").read_text()

    assert "Content-Security-Policy" in html
    assert "default-src 'self'" in html
    assert 'id="signal-map"' in html
    assert 'aria-label="Technocore room activity map"' in html
    assert 'aria-describedby="map-description map-values"' in html
    assert 'id="map-description"' in html
    assert 'id="map-values"' in html
    assert '<main id="main-content">' in html
    assert 'href="#main-content"' in html
    assert 'id="rooms-body"' in html
    assert 'id="projects-grid"' in html
    assert 'id="proofs-grid"' in html
    assert '<script src="validate.js" defer></script>' in html
    assert '<script src="app.js" defer></script>' in html
    assert html.index('src="validate.js"') < html.index('src="app.js"')


def test_frontend_never_injects_remote_markup_and_honors_reduced_motion() -> None:
    javascript = (DOCS / "app.js").read_text()
    validation = (DOCS / "validate.js").read_text()
    public_scripts = javascript + validation

    assert ".innerHTML" not in public_scripts
    assert "eval(" not in public_scripts
    assert "new Function" not in public_scripts
    assert "textContent" in javascript
    assert "createElement" in javascript
    assert "prefers-reduced-motion" in javascript
    assert "safeExternalUrl" in validation
    assert "function validateSnapshot" in validation
    assert "function validateProofs" in validation
    assert "exactKeys" in validation
    assert '"https://github.com", "https://technocore.chat"' in validation
    assert "validateSnapshot(snapshot)" in javascript
    assert "validateProofs(proofs)" in javascript
    assert "clearDynamic" in javascript
    assert "project.server_reported_did" in javascript
    assert "room.coordination_signal_count" in javascript
    assert "room.duplicate_rate" in javascript
    assert 'byId("map-values")' in javascript
    assert "coordination rate" in javascript
    assert "./data/snapshot.json" in javascript
    assert "./data/proofs.json" in javascript


def test_styles_are_responsive_and_expose_keyboard_focus() -> None:
    css = (DOCS / "styles.css").read_text()

    assert ":focus-visible" in css
    assert "@media (max-width: 760px)" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "#00d992" in css


def test_proof_data_states_trust_limitations_without_raw_receipt_body() -> None:
    proofs = json.loads((DOCS / "data" / "proofs.json").read_text())
    serialized = json.dumps(proofs)

    assert proofs["schema_version"] == 1
    assert proofs["items"][0]["project"] == "technocore-hermes"
    assert proofs["items"][0]["technocore_evidence"]["sequence"] == 1495
    assert proofs["items"][0]["technocore_evidence"]["live_state"] == "evicted from rolling room"
    assert proofs["items"][0]["technocore_evidence"]["inclusion_proof"] is False
    assert "hard untrusted-data boundary" not in serialized
    assert "private seed" not in serialized.lower()


def test_metric_and_map_labels_match_rendered_units() -> None:
    html = (DOCS / "index.html").read_text()

    assert "SERVER-REPORTED SIGNED SHARE" in html
    assert "EXACT-DUPLICATE EXTRA RATE" in html
    assert "unique senders" in html
    assert "coordination rate" in html
    assert "duplicate rate" in html
    assert "coordination</span>" not in html
    assert "Messages (count)" in html
    assert "Unique senders (count)" in html
    assert "Top sender share (%)" in html
    assert "Duplicate-extra rate (%)" in html
    assert "Coordination signals (count)" in html
    assert "A DID proves key control" not in html
    assert "A verifiable signature can demonstrate control" in html


def test_pages_workflow_is_pinned_least_privilege_and_off_laptop() -> None:
    workflow = (ROOT / ".github" / "workflows" / "pages.yml").read_text()

    assert "cron: '17 */2 * * *'" in workflow
    assert "contents: read" in workflow
    assert "pages: write" in workflow
    assert "id-token: write" in workflow
    assert "permissions: {}" in workflow
    assert "persist-credentials: false" in workflow
    assert "timeout-minutes: 8" in workflow
    assert "needs: build" in workflow
    assert "enablement: true" not in workflow
    assert "Settings → Pages → Source: GitHub Actions" in (ROOT / "README.md").read_text()
    assert "actions/checkout@11d5960a326750d5838078e36cf38b85af677262" in workflow
    assert "actions/configure-pages@983d7736d9b0ae728b81ab479565c72886d7745b" in workflow
    assert "actions/upload-pages-artifact@56afc609e74202658d3ffba0e8f6dda462b719fa" in workflow
    assert "actions/deploy-pages@d6db90164ac5ed86f2b6aed7e0febac5b3c0c03e" in workflow
    assert "--rooms lobby technocore" in workflow
    assert "--attempts 3" in workflow
    assert "node tests/frontend_validation.cjs" in workflow
    assert (
        "node scripts/validate_public_data.cjs docs/data/snapshot.json docs/data/proofs.json"
        in workflow
    )
