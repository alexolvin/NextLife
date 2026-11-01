#!/usr/bin/env python3
"""Deterministic acceptance evaluator (Section 7.2, Addendum 1).

Strictly evaluates stage readiness, executes quality gates, verifies evidence,
and writes acceptance/state.json.
Vocabulary: DONE, BLOCKED-EXTERNAL, FAILED, NOT-STARTED.
"""

import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
ACCEPTANCE_DIR = ROOT_DIR / "acceptance"
EVIDENCE_DIR = ACCEPTANCE_DIR / "evidence"
TOKENS_DIR = ACCEPTANCE_DIR / "tokens"
QUESTIONS_FILE = ACCEPTANCE_DIR / "questions.md"
CONFIRMATIONS_FILE = ACCEPTANCE_DIR / "owner_confirmations.yaml"
STATE_FILE = ACCEPTANCE_DIR / "state.json"


def compute_evidence_hash() -> str:
    """Compute sha256 across all files in acceptance/evidence/."""
    hasher = hashlib.sha256()
    if not EVIDENCE_DIR.exists():
        return hasher.hexdigest()

    all_files = sorted(p for p in EVIDENCE_DIR.rglob("*") if p.is_file())
    for f in all_files:
        rel = f.relative_to(EVIDENCE_DIR).as_posix().encode("utf-8")
        hasher.update(rel)
        try:
            hasher.update(f.read_bytes())
        except Exception:
            pass
    return hasher.hexdigest()


def check_open_questions() -> dict[str, list[str]]:
    """Parse acceptance/questions.md and find OPEN questions mapped by stage."""
    open_q: dict[str, list[str]] = {}
    if not QUESTIONS_FILE.exists():
        return open_q

    with open(QUESTIONS_FILE, encoding="utf-8") as f:
        for line in f:
            parts = [p.strip() for p in line.strip().split(";")]
            if len(parts) >= 4:
                qid, stage, question, status = parts[0], parts[1], parts[2], parts[3]
                if status.upper() == "OPEN":
                    norm_stage = stage.upper().replace("Э", "E")
                    open_q.setdefault(norm_stage, []).append(f"{qid}: {question}")
    return open_q


def run_gate(cmd: str) -> tuple[bool, str]:
    res = subprocess.run(cmd, shell=True, cwd=ROOT_DIR, capture_output=True, text=True)
    success = res.returncode == 0
    output = (res.stdout + "\n" + res.stderr).strip()
    return success, output


def check_all_gates() -> tuple[bool, dict[str, Any]]:
    gates = {
        "ruff": "ruff check .",
        "mypy": "mypy src",
        "jscpd": "npx jscpd --min-lines 30 --threshold 0 src",
        "check_hardcode": "python3 scripts/check_hardcode.py",
    }
    results: dict[str, Any] = {}
    all_ok = True
    for name, cmd in gates.items():
        ok, out = run_gate(cmd)
        results[name] = {"passed": ok, "summary": out[:200]}
        if not ok:
            all_ok = False
    return all_ok, results


def run_stage_pytest(stage_marker_or_file: str) -> tuple[bool, str]:
    dsn = os.environ.get("NEXTLIFE_DSN")
    if not dsn:
        raise RuntimeError("Missing required environment variable: NEXTLIFE_DSN")
    env = os.environ.copy()
    env["NEXTLIFE_DSN"] = dsn
    env["NEXTLIFE_WEB_TOKEN"] = env.get("NEXTLIFE_WEB_TOKEN", "test_token_secret")

    cmd = f"python3 -m pytest {stage_marker_or_file} -q"
    res = subprocess.run(cmd, shell=True, cwd=ROOT_DIR, env=env, capture_output=True, text=True)
    return (res.returncode == 0), (res.stdout + "\n" + res.stderr).strip()


def evaluate_stage(
    stage_id: str,
    stage_class: str,
    test_target: str,
    prev_stage_token: str | None,
    required_evidence: list[str],
    open_questions: dict[str, list[str]],
    gates_ok: bool,
    gates_details: dict[str, Any],
) -> dict[str, Any]:
    # Check open questions
    q_list = open_questions.get(stage_id.upper(), [])
    if q_list:
        return {
            "class": stage_class,
            "status": "BLOCKED-EXTERNAL",
            "reason": f"Blocked by OPEN questions: {'; '.join(q_list)}",
        }

    # If R-class, check evidence artifacts first
    if stage_class == "R":
        missing_artifacts = []
        for art in required_evidence:
            art_path = EVIDENCE_DIR / stage_id / art
            if not art_path.exists() or art_path.stat().st_size == 0:
                missing_artifacts.append(art)
        if missing_artifacts:
            return {
                "class": stage_class,
                "status": "BLOCKED-EXTERNAL",
                "reason": f"External evidence artifacts missing: {', '.join(missing_artifacts)}",
            }

    # Check previous token
    if prev_stage_token:
        token_path = TOKENS_DIR / f"{prev_stage_token}.token"
        if not token_path.exists():
            return {
                "class": stage_class,
                "status": "NOT-STARTED" if stage_class == "L" else "BLOCKED-EXTERNAL",
                "reason": f"Awaiting previous stage acceptance token '{prev_stage_token}.token' from Owner",
            }

    # Check gates
    if not gates_ok:
        failed_gates = [k for k, v in gates_details.items() if not v["passed"]]
        return {
            "class": stage_class,
            "status": "FAILED",
            "reason": f"Quality gates failed: {', '.join(failed_gates)}",
        }

    # Run tests
    test_ok, test_out = run_stage_pytest(test_target)
    if not test_ok:
        return {
            "class": stage_class,
            "status": "FAILED",
            "reason": f"Tests failed: {test_out[:200]}",
        }

    return {
        "class": stage_class,
        "status": "DONE",
        "reason": "All checks and evidence verified successfully",
    }


def compute_state_hash(state_dict: dict[str, Any]) -> str:
    """Compute sha256 of canonical state content excluding state_hash."""
    clean = {k: v for k, v in state_dict.items() if k != "state_hash"}
    canonical = json.dumps(clean, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def verify_state_integrity(state_file: Path | None = None) -> tuple[bool, str]:
    """Verify integrity of state.json against its recorded state_hash."""
    target_file = state_file or STATE_FILE
    if not target_file.exists():
        return False, "state.json does not exist"
    try:
        data = json.loads(target_file.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"Failed to parse state.json: {exc}"

    recorded_hash = data.get("state_hash")
    if not recorded_hash:
        return False, "Missing state_hash in state.json"

    expected_hash = compute_state_hash(data)
    if recorded_hash != expected_hash:
        return False, f"Tampered state.json: hash mismatch (recorded {recorded_hash}, expected {expected_hash})"

    return True, "Integrity verified"


def run_acceptance() -> dict[str, Any]:
    ACCEPTANCE_DIR.mkdir(parents=True, exist_ok=True)
    TOKENS_DIR.mkdir(parents=True, exist_ok=True)
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

    evidence_hash = compute_evidence_hash()
    open_questions = check_open_questions()
    gates_ok, gates_details = check_all_gates()

    stage_definitions = [
        ("E0", "L", "tests/test_stage_e0.py", None, []),
        ("E1", "L", "tests/test_stage_e1.py", "E0", []),
        ("E2", "R", "tests/test_stage_e2.py", "E1", ["fx_ecb_raw.xml", "fx_rows.json"]),
        ("E3-L", "L", "tests/test_stage_e3.py", "E2", []),
        ("E3-R", "R", "tests/test_stage_e3.py", "E3-L", ["raw_search_bazos.html"]),
        ("E4-L", "L", "tests/test_stage_e4.py", "E3-L", []),
        ("E4-R", "R", "tests/test_stage_e4.py", "E4-L", ["vlm_verdict.json"]),
        (
            "E5",
            "R",
            "tests/test_stage_e5.py",
            "E4-L",
            ["published_listing.json", "external_url.txt"],
        ),
        ("E6", "R", "tests/test_stage_e6.py", "E5", ["message_id.txt", "delivery_evidence.json"]),
        ("E7", "L", "tests/test_stage_e7.py", "E6", []),
        ("E8", "R", "tests/test_stage_e8.py", "E7", ["closure_evidence.json"]),
        ("E9", "R", "tests/test_stage_e9.py", "E8", ["e2e_run.json", "truth_log_export.json"]),
    ]

    stages_state: dict[str, Any] = {}
    for sid, sclass, ttarget, ptoken, revidence in stage_definitions:
        stages_state[sid] = evaluate_stage(
            stage_id=sid,
            stage_class=sclass,
            test_target=ttarget,
            prev_stage_token=ptoken,
            required_evidence=revidence,
            open_questions=open_questions,
            gates_ok=gates_ok,
            gates_details=gates_details,
        )

    state = {
        "updated_at": datetime.now(UTC).isoformat(),
        "evidence_hash": evidence_hash,
        "quality_gates": gates_details,
        "stages": stages_state,
    }
    state["state_hash"] = compute_state_hash(state)

    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    return state


def main() -> None:
    if "--verify" in sys.argv:
        ok, msg = verify_state_integrity()
        if not ok:
            sys.stdout.write(json.dumps({"status": "FAILED", "error": msg}, ensure_ascii=False, indent=2) + "\n")
            sys.exit(1)
        else:
            sys.stdout.write(json.dumps({"status": "OK", "message": msg}, ensure_ascii=False, indent=2) + "\n")
            sys.exit(0)

    state = run_acceptance()
    sys.stdout.write(json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
