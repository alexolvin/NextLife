import json
import subprocess
import sys

from scripts.acceptance import (
    compute_state_hash,
    evaluate_stage,
    verify_state_integrity,
)


def test_stage_with_open_questions_blocked():
    """Verify that any stage with an open question cannot exceed BLOCKED-EXTERNAL status."""
    res = evaluate_stage(
        stage_id="E1",
        stage_class="L",
        test_target="tests/test_stage_e1.py",
        prev_stage_token=None,
        required_evidence=[],
        open_questions={"E1": ["Q-01: Is feature X approved?"]},
        gates_ok=True,
        gates_details={},
    )
    assert res["status"] == "BLOCKED-EXTERNAL"
    assert "Blocked by OPEN questions" in res["reason"]


def test_state_json_tamper_detected(tmp_path):
    """Verify that manual tampering of state.json causes hash mismatch and FAILED verification."""
    state_file = tmp_path / "state.json"
    data = {
        "updated_at": "2026-09-08T00:00:00Z",
        "evidence_hash": "abc",
        "quality_gates": {},
        "stages": {"E0": {"status": "NOT-STARTED"}},
    }
    data["state_hash"] = compute_state_hash(data)
    state_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    # Untampered passes
    ok, msg = verify_state_integrity(state_file)
    assert ok is True
    assert msg == "Integrity verified"

    # Manually tamper with state
    data["stages"]["E0"]["status"] = "DONE"
    # Write without updating state_hash
    state_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    ok, msg = verify_state_integrity(state_file)
    assert ok is False
    assert "Tampered state.json: hash mismatch" in msg


def test_acceptance_verify_cli(tmp_path, monkeypatch):
    """Verify CLI exit code and output on verify flag."""
    proc = subprocess.run(
        [sys.executable, "scripts/acceptance.py", "--verify"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    res = json.loads(proc.stdout)
    assert res["status"] == "OK"
