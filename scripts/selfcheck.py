#!/usr/bin/env python3
"""Automated self-verification runner for NextLife (Task K1, Phase F2).

Runs 49 behavioral checks (C-01 .. C-49) against an isolated test environment.
Result statuses are computed strictly from on-disk artifacts.
Exit code equals the number of FAIL checks.
"""

import argparse
from datetime import UTC, datetime
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any
import httpx

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR / "src") not in sys.path:
    sys.path.insert(0, str(ROOT_DIR / "src"))
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

lib_path = str(ROOT_DIR / "lib" / "playwright_libs")
if Path(lib_path).exists():
    existing_ld = os.environ.get("LD_LIBRARY_PATH", "")
    os.environ["LD_LIBRARY_PATH"] = f"{lib_path}:{existing_ld}".strip(":")

from scripts.selfcheck_audit import get_audit_checks
from scripts.selfcheck_desc import get_desc_checks
from scripts.selfcheck_env import get_env_checks
from scripts.selfcheck_lots import get_lots_checks
from scripts.selfcheck_media_advanced import get_media_advanced_checks
from scripts.selfcheck_media_core import get_media_core_checks
from scripts.selfcheck_media_ops import get_media_ops_checks
from scripts.selfcheck_media_view import get_media_view_checks
from scripts.selfcheck_types import CheckContext, CheckDef, CheckResult, evaluate_artifacts

ROOT_DIR = Path(__file__).resolve().parent.parent


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def compute_artifacts_hash(directory: Path) -> str:
    h = hashlib.sha256()
    for root, _, files in os.walk(directory):
        for f in sorted(files):
            if f in ("selfcheck.json", "selfcheck.md"):
                continue
            fp = Path(root) / f
            try:
                h.update(fp.read_bytes())
            except Exception:
                pass
    return h.hexdigest()


def get_git_revision() -> str:
    try:
        res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT_DIR), capture_output=True, text=True)
        return res.stdout.strip()[:10] if res.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def clean_test_database(test_dsn: str) -> dict[str, int]:
    """Clean all application data from test database and verify 0 rows before selfcheck (K1-054).

    The DSN is resolved from the process environment (run_selfcheck sets
    NEXTLIFE_DSN before calling this); test_dsn is retained for signature stability.
    """
    from sqlalchemy import text

    from nextlife.storage.db import get_engine

    tables = [
        "deal", "listing", "item_photo", "media_variant", "media_master", "media_original",
        "item_defect", "price_history", "lot_pricing", "lot", "approval", "research_result",
        "research_run", "buy_request", "buy_candidate", "message", "thread", "outbox", "item",
        "audit_event", "truth_log"
    ]
    # TRUNCATE (writes): one committed transaction.
    with get_engine().begin() as conn:
        for tbl in tables:
            conn.execute(text(f"TRUNCATE TABLE {tbl} CASCADE;"))
    # row counts (reads)
    counts: dict[str, int] = {}
    with get_engine().connect() as conn:
        for tbl in tables:
            counts[tbl] = conn.execute(text(f"SELECT count(*) FROM {tbl};")).fetchone()[0]
    return counts





def collect_all_checks() -> list[CheckDef]:
    """Collect all 49 checks across all subsystems."""
    checks: list[CheckDef] = []
    checks.extend(get_env_checks())
    checks.extend(get_lots_checks())
    checks.extend(get_media_core_checks())
    checks.extend(get_media_ops_checks())
    checks.extend(get_media_view_checks())
    checks.extend(get_media_advanced_checks())
    checks.extend(get_desc_checks())
    checks.extend(get_audit_checks())
    return checks


def verify_checks_integrity(checks: list[CheckDef]) -> None:
    """Strict fullness control: require exactly all 49 check IDs (C-01 .. C-49) (K1-052)."""
    expected = {f"C-{i:02d}" for i in range(1, 50)}
    present = {c.id for c in checks}
    if present != expected:
        missing = sorted(expected - present)
        extra = sorted(present - expected)
        raise RuntimeError(f"Selfcheck integrity failure: missing={missing}, extra={extra}")


def run_selfcheck(out_dir: Path, test_dsn: str) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    os.environ["NEXTLIFE_DSN"] = test_dsn
    temp_dir = Path(tempfile.mkdtemp(prefix="nextlife_selfcheck_"))
    media_dir = temp_dir / "media"
    media_dir.mkdir(parents=True, exist_ok=True)

    # Clean database before running checks (K1-054)
    db_counts = clean_test_database(test_dsn)
    all_zero = all(c == 0 for c in db_counts.values())
    out_dir.joinpath("clean_db_check.txt").write_text(
        f"clean={all_zero}\ncounts={json.dumps(db_counts, indent=2)}\n", encoding="utf-8"
    )
    print(f"Clean database check: verified all media, lots, and deals tables are empty (0 rows): {all_zero}")

    auth_token = os.environ.get("NEXTLIFE_WEB_TOKEN", "selfcheck_token_secret_123")
    oc_token = os.environ.get("NEXTLIFE_OPENCLAW_TOKEN", "selfcheck_oc_token_secret_123")
    port = find_free_port()
    base_url = f"http://127.0.0.1:{port}"

    print(f"=== Starting NextLife Selfcheck Runner ===")
    print(f"Base URL: {base_url}")
    print(f"Test DSN: {test_dsn}")
    print(f"Output Directory: {out_dir}")

    # Launch server in background
    server_env = os.environ.copy()
    server_env["NEXTLIFE_DSN"] = test_dsn
    server_env["NEXTLIFE_WEB_TOKEN"] = auth_token
    server_env["NEXTLIFE_OPENCLAW_TOKEN"] = oc_token
    existing_pp = os.environ.get("PYTHONPATH", "")
    server_env["PYTHONPATH"] = f"{ROOT_DIR / 'src'}:{existing_pp}" if existing_pp else str(ROOT_DIR / "src")

    server_cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "nextlife.api.main:create_app",
        "--factory",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--log-level",
        "warning",
    ]
    server_proc = subprocess.Popen(server_cmd, cwd=str(ROOT_DIR), env=server_env)

    # Wait for server readiness
    ready = False
    for _ in range(30):
        try:
            with httpx.Client(timeout=1.0) as client:
                r = client.get(f"{base_url}/ui/items?token={auth_token}")
                if r.status_code in (200, 303, 302, 401):
                    ready = True
                    break
        except Exception:
            pass
        time.sleep(0.3)

    if not ready:
        print("ERROR: Ephemeral test server failed to start!", file=sys.stderr)
        server_proc.kill()
        return 99

    print("Ephemeral server is active and ready. Executing 49 checks...")

    ctx = CheckContext(
        base_url=base_url,
        test_dsn=test_dsn,
        auth_token=auth_token,
        out_dir=out_dir,
        media_dir=media_dir,
        temp_dir=temp_dir,
        oc_token=oc_token,
    )

    all_checks = collect_all_checks()
    try:
        verify_checks_integrity(all_checks)
    except RuntimeError as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        server_proc.kill()
        raise

    results: list[CheckResult] = []

    try:
        for check in all_checks:
            art_dir = out_dir / check.id
            art_dir.mkdir(parents=True, exist_ok=True)
            meta: dict[str, Any] = {}
            try:
                meta = check.runner(ctx, art_dir) or {}
            except Exception as exc:
                art_dir.joinpath("error.txt").write_text(f"Execution exception: {type(exc).__name__}: {exc}\n", encoding="utf-8")
                meta = {"exception": str(exc)}

            status, reason, artifacts = evaluate_artifacts(art_dir, check, meta)
            res = CheckResult(
                id=check.id,
                category=check.category,
                title=check.title,
                status=status,
                reason=reason,
                artifacts=artifacts,
            )
            results.append(res)
            print(f"[{res.status:<5}] {check.id}: {check.title} -> {reason[:70]}")
    finally:
        server_proc.kill()
        server_proc.wait()
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)

    # Compile reports
    art_hash = compute_artifacts_hash(out_dir)
    run_date = datetime.now(UTC).isoformat()
    git_sha = get_git_revision()
    run_id = f"selfcheck-{uuid.uuid4().hex[:8]}"

    pass_count = sum(1 for r in results if r.status == "PASS")
    fail_count = sum(1 for r in results if r.status == "FAIL")
    blocked_count = sum(1 for r in results if r.status == "BLOCKED")

    # 1. selfcheck.json
    json_data = {
        "run_id": run_id,
        "run_date": run_date,
        "code_version": git_sha,
        "artifacts_hash": art_hash,
        "total_checks": len(results),
        "passed": pass_count,
        "failed": fail_count,
        "blocked": blocked_count,
        "checks": [
            {
                "id": r.id,
                "category": r.category,
                "title": r.title,
                "status": r.status,
                "reason": r.reason,
                "artifacts": r.artifacts,
            }
            for r in results
        ],
    }
    out_dir.joinpath("selfcheck.json").write_text(json.dumps(json_data, ensure_ascii=False, indent=2), encoding="utf-8")

    # 2. selfcheck.md
    md_lines = [
        f"# Отчёт самопроверочного прогона NextLife ({run_id})",
        "",
        f"- **Дата запуска**: `{run_date}`",
        f"- **Версия кода (git)**: `{git_sha}`",
        f"- **Хэш артефактов (sha256)**: `{art_hash}`",
        f"- **Итог**: **{pass_count} PASS**, **{fail_count} FAIL**, **{blocked_count} BLOCKED** (всего {len(results)})",
        "",
        "| ID | Раздел | Название | Статус | Причина / Доказательство | Артефакты |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in results:
        status_md = f"**{r.status}**" if r.status == "FAIL" else r.status
        arts_md = ", ".join(r.artifacts[:4])
        if len(r.artifacts) > 4:
            arts_md += f" (+{len(r.artifacts) - 4})"
        reason_clean = r.reason.replace("|", "/")
        md_lines.append(f"| `{r.id}` | {r.category} | {r.title} | {status_md} | {reason_clean} | `{arts_md}` |")

    md_lines.append("")
    out_dir.joinpath("selfcheck.md").write_text("\n".join(md_lines), encoding="utf-8")

    print("\n=== Selfcheck Completed ===")
    print(f"Summary: {pass_count} PASS, {fail_count} FAIL, {blocked_count} BLOCKED (Exit code: {fail_count})")
    print(f"Artifacts: {out_dir}/selfcheck.md and {out_dir}/selfcheck.json")
    return fail_count


def main() -> int:
    parser = argparse.ArgumentParser(description="NextLife Selfcheck Verification Runner")
    parser.add_argument("--out", default="correction/selfcheck", help="Directory to store artifacts and reports")
    args = parser.parse_args()

    test_dsn = os.environ.get("NEXTLIFE_DSN")
    if not test_dsn:
        print("FATAL: Missing required environment variable: NEXTLIFE_DSN", file=sys.stderr)
        return 1

    return run_selfcheck(Path(args.out).resolve(), test_dsn)


if __name__ == "__main__":
    sys.exit(main())
