#!/usr/bin/env python3
"""Daemon manager for NextLife Web-UI server."""

import argparse
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

# Repo root, derived from this file's location (scripts/ -> root). Paths are
# repo-relative so the launcher is portable and carries no absolute home paths.
ROOT = Path(__file__).resolve().parent.parent

PID_FILE = ROOT / "data" / "server.pid"
LOG_FILE = ROOT / "data" / "server.log"
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8081
DEFAULT_TOKEN = os.environ.get("NEXTLIFE_WEB_TOKEN", "")


def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def get_running_pid() -> int | None:
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            os.kill(pid, 0)
            return pid
        except (ValueError, OSError):
            PID_FILE.unlink(missing_ok=True)
    return None


def start_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> bool:
    existing_pid = get_running_pid()
    if existing_pid:
        print(f"Server is already running with PID {existing_pid} on port {port}")
        return True

    if is_port_in_use(port):
        print(f"Port {port} is already in use by another process.")
        return False

    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    dsn = os.environ.get("NEXTLIFE_DSN")
    if not dsn:
        print("Missing required environment variable: NEXTLIFE_DSN")
        return False

    # The web token is NOT injected here: the app resolves it at runtime via
    # resolve_secret (process environment first, then the project-root .env).
    # Force-setting an empty value here would shadow the .env fallback.
    env = os.environ.copy()
    env.setdefault("NEXTLIFE_DSN", dsn)

    # The launcher runs from the project .venv (sys.executable), which already
    # carries every dependency. PYTHONPATH only needs the repo src/ tree.
    src_dir = str(ROOT / "src")
    env["PYTHONPATH"] = f"{src_dir}:{env.get('PYTHONPATH', '')}".rstrip(":")

    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "nextlife.api.main:create_app",
        "--factory",
        "--host",
        host,
        "--port",
        str(port),
    ]

    log_fd = open(LOG_FILE, "a", encoding="utf-8")
    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        env=env,
        stdout=log_fd,
        stderr=log_fd,
        start_new_session=True,
    )

    PID_FILE.write_text(str(proc.pid), encoding="utf-8")

    # Wait for server to start
    for _ in range(50):
        time.sleep(0.1)
        if is_port_in_use(port):
            try:
                req = Request(f"http://127.0.0.1:{port}/health")
                with urlopen(req, timeout=1.0) as resp:
                    if resp.status == 200:
                        print(f"NextLife server started successfully (PID: {proc.pid})")
                        print(f"  Local URL:     http://127.0.0.1:{port}/ui")
                        return True
            except Exception:
                pass

    print("Server failed to respond within 5 seconds. Check data/server.log for details.")
    return False


def stop_server() -> bool:
    pid = get_running_pid()
    if not pid:
        print("No running NextLife server found.")
        PID_FILE.unlink(missing_ok=True)
        return True

    try:
        os.kill(pid, signal.SIGTERM)
        for _ in range(30):
            time.sleep(0.1)
            try:
                os.kill(pid, 0)
            except OSError:
                break
        else:
            os.kill(pid, signal.SIGKILL)
        print(f"Stopped server process {pid}")
    except OSError as e:
        print(f"Failed to stop PID {pid}: {e}")
    finally:
        PID_FILE.unlink(missing_ok=True)
    return True


def status_server(port: int = DEFAULT_PORT) -> None:
    pid = get_running_pid()
    port_open = is_port_in_use(port)
    print(f"Server PID:     {pid if pid else 'NOT RUNNING'}")
    print(f"Port {port}:    {'LISTENING' if port_open else 'CLOSED'}")
    if port_open:
        try:
            token = os.environ.get("NEXTLIFE_WEB_TOKEN", DEFAULT_TOKEN)
            req = Request(f"http://127.0.0.1:{port}/health")
            with urlopen(req, timeout=1.0) as resp:
                print(f"Health check:   HTTP {resp.status}")
            req_items = Request(f"http://127.0.0.1:{port}/ui/items?token={token}")
            with urlopen(req_items, timeout=1.0) as resp:
                print(f"UI items check: HTTP {resp.status}")
        except Exception as e:
            print(f"Health check error: {e}")


def main() -> None:
    parser = argparse.ArgumentParser(description="NextLife server controller")
    parser.add_argument("action", choices=["start", "stop", "restart", "status"], default="start", nargs="?")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default=DEFAULT_HOST)
    args = parser.parse_args()

    if args.action == "start":
        start_server(args.host, args.port)
    elif args.action == "stop":
        stop_server()
    elif args.action == "restart":
        stop_server()
        start_server(args.host, args.port)
    elif args.action == "status":
        status_server(args.port)


if __name__ == "__main__":
    main()
