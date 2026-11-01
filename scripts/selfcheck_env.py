"""Environment and isolation checks (C-01 .. C-05, C-49) for selfcheck."""

import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from sqlalchemy import text

from nextlife.storage.db import get_engine

from scripts.selfcheck_types import CheckContext, CheckDef

ROOT_DIR = Path(__file__).resolve().parent.parent


def run_c01(ctx: CheckContext, art_dir: Path) -> dict[str, Any]:
    """C-01: Startup without NEXTLIFE_DSN fails with variable name in message."""
    cmd = [sys.executable, "scripts/migrate.py"]
    env = os.environ.copy()
    env.pop("NEXTLIFE_DSN", None)
    res = subprocess.run(cmd, cwd=str(ROOT_DIR), env=env, capture_output=True, text=True)

    art_dir.joinpath("command.txt").write_text(" ".join(cmd) + " (without NEXTLIFE_DSN)\n", encoding="utf-8")
    out = res.stdout + res.stderr
    art_dir.joinpath("output.txt").write_text(out, encoding="utf-8")
    return {"returncode": res.returncode, "output": out}


def validate_c01(art_dir: Path, meta: dict[str, Any]) -> tuple[bool, str]:
    out_file = art_dir / "output.txt"
    if not out_file.exists() or out_file.stat().st_size == 0:
        return False, "Missing or empty output.txt"
    text = out_file.read_text(encoding="utf-8")
    if meta.get("returncode") != 0 and "NEXTLIFE_DSN" in text:
        return True, "Process exited with error and named NEXTLIFE_DSN"
    return False, f"Expected non-zero exit and NEXTLIFE_DSN in message, got code {meta.get('returncode')}"


def run_c02(ctx: CheckContext, art_dir: Path) -> dict[str, Any]:
    """C-02: A non-test database address is refused at the pre-connection fuse.

    The child pytest receives a NEXTLIFE_DSN whose database is
    `nextlife_guardprobe` (a non-test name). The conftest fuse must refuse it
    BEFORE any engine/connection is created: the output carries the fuse
    message (naming the DB and the _test rule) and NO driver/connection error.
    """
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(ctx.test_dsn)
    probe_dsn = urlunsplit(parts._replace(path="/nextlife_guardprobe"))

    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "tests/test_db_guard.py::test_guard_allows_test_database",
    ]
    env = os.environ.copy()
    env["NEXTLIFE_DSN"] = probe_dsn
    res = subprocess.run(cmd, cwd=str(ROOT_DIR), env=env, capture_output=True, text=True)

    art_dir.joinpath("command.txt").write_text(
        " ".join(cmd) + " (NEXTLIFE_DSN database=nextlife_guardprobe)\n", encoding="utf-8"
    )
    art_dir.joinpath("probe_dsn.txt").write_text(probe_dsn + "\n", encoding="utf-8")
    out = res.stdout + res.stderr
    art_dir.joinpath("output.txt").write_text(out, encoding="utf-8")
    return {"returncode": res.returncode, "output": out, "probe_dsn": probe_dsn}


def validate_c02(art_dir: Path, meta: dict[str, Any]) -> tuple[bool, str]:
    out_file = art_dir / "output.txt"
    if not out_file.exists() or out_file.stat().st_size == 0:
        return False, "Missing or empty output.txt"
    text = out_file.read_text(encoding="utf-8")
    if meta.get("returncode") == 0:
        return False, "Child pytest unexpectedly succeeded (guard not enforced)"
    # First-line fuse must have fired: the refusal message names the probe DB
    # and states the _test rule.
    if "nextlife_guardprobe" not in text or "does not end with '_test'" not in text:
        return False, "First-line fuse message (probe DB name + '_test' rule) absent from output"
    # No connection was attempted: no driver/connection error may be present.
    driver_markers = [
        "peer authentication",
        "password authentication",
        "failed to connect to test database",
        "psycopg2.operationalerror",
        "connection to server",
        "could not connect",
        "connection refused",
        "fatal:",
    ]
    hits = [m for m in driver_markers if m in text.lower()]
    if hits:
        return False, f"Driver/connection error present (fuse did not refuse first): {hits}"
    return True, "Aborted at the pre-connection fuse before any connection attempt (DB nextlife_guardprobe)"


def run_c03(ctx: CheckContext, art_dir: Path) -> dict[str, Any]:
    """C-03: Search for connection strings across project source/test/script/config files."""
    cmd = ["grep", "-rnIE", "postgres(ql)?://", "src", "scripts", "tests", "config"]
    res = subprocess.run(cmd, cwd=str(ROOT_DIR), capture_output=True, text=True)

    art_dir.joinpath("command.txt").write_text(" ".join(cmd) + "\n", encoding="utf-8")
    out = (res.stdout + res.stderr).strip()
    art_dir.joinpath("output.txt").write_text(out + "\n" if out else "0 matches\n", encoding="utf-8")
    return {"returncode": res.returncode, "match_count": len(res.stdout.strip().splitlines()) if res.stdout.strip() else 0}


def validate_c03(art_dir: Path, meta: dict[str, Any]) -> tuple[bool, str]:
    out_file = art_dir / "output.txt"
    if not out_file.exists():
        return False, "Missing output.txt"
    if meta.get("match_count") == 0:
        return True, "Zero matches found in project directories"
    return False, f"Found {meta.get('match_count')} connection string match(es)"


def run_c04(ctx: CheckContext, art_dir: Path) -> dict[str, Any]:
    """C-04: Attempt to connect as the project role to a FOREIGN database.

    The check is only meaningful when the target database actually exists: a typo
    in the target name would surface as "database does not exist" and read as a
    pass forever. So we first confirm the target exists via pg_database (on the
    test connection); only an authentication or CONNECT-privilege refusal against
    an existing database is a valid PASS.
    """
    from urllib.parse import urlsplit, urlunsplit

    from sqlalchemy import create_engine

    foreign_db = "propertyboss"
    parsed = urlsplit(ctx.test_dsn)

    # 1. Confirm the target database exists (test connection, read-only).
    exists = False
    exists_error = ""
    try:
        with create_engine(ctx.test_dsn).connect() as conn:
            row = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :d"), {"d": foreign_db}
            ).fetchone()
            exists = row is not None
    except Exception as exc:
        exists_error = f"{type(exc).__name__}: {exc}"
    art_dir.joinpath("pg_database_check.txt").write_text(
        f"target={foreign_db}\nexists={exists}\nerror={exists_error}\n", encoding="utf-8"
    )

    # 2. Attempt the foreign connection (meaningful only if the target exists).
    # One-off engine aimed at the FOREIGN database (not the app DSN): keep the
    # SQLAlchemy driver suffix — create_engine/psycopg2 handle it natively, so no
    # driver-stripping is needed and the raw-psycopg2 ban stays at zero.
    foreign_url = urlunsplit((parsed.scheme, parsed.netloc, f"/{foreign_db}", parsed.query, parsed.fragment))
    art_dir.joinpath("query.txt").write_text("SELECT * FROM objects LIMIT 1;\n", encoding="utf-8")
    try:
        with create_engine(foreign_url).connect() as conn:
            rows = conn.execute(text("SELECT * FROM objects LIMIT 1;")).fetchall()
        art_dir.joinpath("error.txt").write_text(f"UNEXPECTED_SUCCESS: {rows}\n", encoding="utf-8")
        return {"denied": False, "error": "", "exists": exists, "exists_error": exists_error}
    except Exception as exc:
        err_msg = f"{type(exc).__name__}: {exc}"
        art_dir.joinpath("error.txt").write_text(err_msg + "\n", encoding="utf-8")
        return {"denied": True, "error": err_msg, "exists": exists, "exists_error": exists_error}


def validate_c04(art_dir: Path, meta: dict[str, Any]) -> tuple[bool, str]:
    err_file = art_dir / "error.txt"
    if not err_file.exists() or err_file.stat().st_size == 0:
        return False, "Missing error.txt"
    # The target database must exist; otherwise the check is misconfigured — a
    # typo in the target name would otherwise read as a pass forever.
    if not meta.get("exists"):
        return False, (
            "проверка настроена неверно: целевая база не существует "
            f"(возможна опечатка в имени базы): {meta.get('exists_error', '')}".strip()
        )
    text = err_file.read_text(encoding="utf-8")
    if not meta.get("denied"):
        return False, "Access to the foreign database SUCCEEDED — isolation is broken"
    # PASS only on an authentication refusal or a CONNECT-privilege refusal
    # against an existing database — not on a generic "does not exist" / network
    # error, which would mask a misconfigured check.
    lowered = text.lower()
    auth_markers = [
        "peer authentication failed",
        "password authentication failed",
        "no pg_hba.conf entry",
        "not permitted to log in",
        "role does not exist",
    ]
    conn_priv_markers = ["permission denied for database"]
    if any(m in lowered for m in auth_markers) or any(m in lowered for m in conn_priv_markers):
        return True, (
            "Server refused the foreign-DB connection as expected "
            f"(auth/CONNECT-privilege): {text.strip()[:140]}"
        )
    return False, (
        "Connection failed but the reason is neither an authentication nor a "
        f"CONNECT-privilege refusal: {text.strip()[:140]}"
    )


def run_c05(ctx: CheckContext, art_dir: Path) -> dict[str, Any]:
    """C-05: Migrations from scratch on empty database and idempotence check."""
    cmd = [sys.executable, "scripts/migrate.py"]
    env = os.environ.copy()
    env["NEXTLIFE_DSN"] = ctx.test_dsn
    res1 = subprocess.run(cmd, cwd=str(ROOT_DIR), env=env, capture_output=True, text=True)
    res2 = subprocess.run(cmd, cwd=str(ROOT_DIR), env=env, capture_output=True, text=True)

    art_dir.joinpath("migrate_output.txt").write_text(
        f"RUN 1 (exit {res1.returncode}):\n{res1.stdout}{res1.stderr}\n\nRUN 2 (exit {res2.returncode}):\n{res2.stdout}{res2.stderr}",
        encoding="utf-8",
    )

    # Check tables (read: test DB via the single engine)
    with get_engine().connect() as conn:
        tables = sorted([r[0] for r in conn.execute(text("SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname = 'public';")).fetchall()])
    art_dir.joinpath("tables_list.txt").write_text("\n".join(tables), encoding="utf-8")

    return {
        "run1_code": res1.returncode,
        "run2_code": res2.returncode,
        "table_count": len(tables),
    }


def validate_c05(art_dir: Path, meta: dict[str, Any]) -> tuple[bool, str]:
    tbl_file = art_dir / "tables_list.txt"
    if not tbl_file.exists() or tbl_file.stat().st_size == 0:
        return False, "Missing or empty tables_list.txt"
    tables = [line.strip() for line in tbl_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    if meta.get("run1_code") == 0 and meta.get("run2_code") == 0 and len(tables) >= 20:
        return True, f"Migrations idempotent, {len(tables)} tables present in public schema"
    return False, f"Migration failed or insufficient tables: {len(tables)} tables"


def run_c49(ctx: CheckContext, art_dir: Path) -> dict[str, Any]:
    """C-49: core.hooksPath points at scripts/hooks (the pre-commit secret guard
    is wired in this clone)."""
    cmd = ["git", "config", "core.hooksPath"]
    res = subprocess.run(cmd, cwd=str(ROOT_DIR), capture_output=True, text=True)
    hooks_path = res.stdout.strip()
    art_dir.joinpath("command.txt").write_text(" ".join(cmd) + "\n", encoding="utf-8")
    art_dir.joinpath("output.txt").write_text(hooks_path + "\n", encoding="utf-8")
    return {"returncode": res.returncode, "hooks_path": hooks_path}


def validate_c49(art_dir: Path, meta: dict[str, Any]) -> tuple[bool, str]:
    out_file = art_dir / "output.txt"
    if not out_file.exists():
        return False, "Missing output.txt"
    hooks_path = out_file.read_text(encoding="utf-8").strip()
    if hooks_path == "scripts/hooks":
        return True, "core.hooksPath is wired to scripts/hooks (pre-commit guard active)"
    return False, (
        f"core.hooksPath = {hooks_path!r} (expected 'scripts/hooks'); wire it with: "
        "git config core.hooksPath scripts/hooks"
    )


def get_env_checks() -> list[CheckDef]:
    return [
        CheckDef("C-01", "Среда", "Запуск без NEXTLIFE_DSN", "Процесс падает, в сообщении имя переменной", run_c01, ["command.txt", "output.txt"], validate_c01),
        CheckDef("C-02", "Среда", "Подключение к базе без _test в тестовом режиме", "Прогон прерывается до записи", run_c02, ["command.txt", "output.txt"], validate_c02),
        CheckDef("C-03", "Среда", "Поиск строк подключения в файлах проекта", "Ноль совпадений", run_c03, ["command.txt", "output.txt"], validate_c03),
        CheckDef("C-04", "Среда", "Попытка подключиться к чужой базе (база существует)", "Отказ сервера на auth/CONNECT при существующей целевой базе", run_c04, ["pg_database_check.txt", "query.txt", "error.txt"], validate_c04),
        CheckDef("C-05", "Среда", "Миграции с нуля и повторный запуск", "Все таблицы созданы, повторный запуск идемпотентен", run_c05, ["migrate_output.txt", "tables_list.txt"], validate_c05),
        CheckDef("C-49", "Среда", "core.hooksPath указывает на scripts/hooks", "Хук pre-commit подключён (core.hooksPath == scripts/hooks)", run_c49, ["command.txt", "output.txt"], validate_c49),
    ]
