#!/usr/bin/env python3
"""Preflight checker for NextLife acceptance (Section 7.1, Addendum 1).

Reads acceptance/owner_confirmations.yaml and verifies external dependencies.
Outputs JSON to stdout.
Exit codes:
  0  - All external dependencies available for R-stages.
  10 - One or more external dependencies missing/null, stages restricted to BLOCKED-EXTERNAL.
"""

import json
import sys
from pathlib import Path
from typing import Any

import yaml


def check_preflight() -> tuple[dict[str, Any], int]:
    root_dir = Path(__file__).resolve().parent.parent
    confirmations_path = root_dir / "acceptance" / "owner_confirmations.yaml"

    deps: dict[str, Any] = {}
    missing: list[str] = []

    if not confirmations_path.exists():
        return {
            "available": False,
            "error": "acceptance/owner_confirmations.yaml does not exist. Awaiting Owner to create and populate it per Addendum 1 Section 5.",
            "dependencies": {},
            "missing": ["acceptance/owner_confirmations.yaml"],
        }, 10

    try:
        with open(confirmations_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as exc:
        return {
            "available": False,
            "error": f"Failed to read acceptance/owner_confirmations.yaml: {exc}",
            "dependencies": {},
            "missing": ["valid_owner_confirmations_yaml"],
        }, 10

    # 1. Base currency
    base_currency = data.get("base_currency")
    if base_currency in ("EUR", "CZK"):
        deps["base_currency"] = {"available": True, "value": base_currency}
    else:
        deps["base_currency"] = {
            "available": False,
            "reason": f"base_currency is null or invalid ({base_currency})",
        }
        missing.append("base_currency")

    # 2. Marketplaces
    marketplaces = data.get("marketplaces") or []
    if not marketplaces:
        deps["marketplaces"] = {
            "available": False,
            "reason": "marketplaces list is empty or null in owner_confirmations.yaml",
        }
        missing.append("marketplaces")
    else:
        mps_status: dict[str, Any] = {}
        all_mps_ok = True
        for mp in marketplaces:
            key = mp.get("key")
            mode = mp.get("access_mode")
            acc = mp.get("account_exists")
            if not key or mode is None or acc is None:
                mps_status[str(key or "unknown")] = {
                    "available": False,
                    "reason": "key, access_mode, or account_exists is null",
                }
                all_mps_ok = False
            else:
                mps_status[key] = {
                    "available": True,
                    "access_mode": mode,
                    "account_exists": acc,
                }
        deps["marketplaces"] = {
            "available": all_mps_ok,
            "details": mps_status,
        }
        if not all_mps_ok:
            missing.append("marketplaces")

    # 3. Channels delegation
    channels = data.get("channels") or {}
    email_delegated = channels.get("email_delegated")
    outbox_interval = channels.get("outbox_pull_interval")
    if email_delegated is True and outbox_interval is not None:
        deps["channels"] = {
            "available": True,
            "email_delegated": True,
            "outbox_pull_interval": outbox_interval,
        }
    else:
        deps["channels"] = {
            "available": False,
            "reason": "channels.email_delegated or outbox_pull_interval is null",
        }
        missing.append("channels")

    # 4. Android
    android = data.get("android") or {}
    bridge_endpoint = android.get("bridge_endpoint")
    if bridge_endpoint:
        deps["android_bridge"] = {"available": True, "endpoint": bridge_endpoint}
    else:
        deps["android_bridge"] = {
            "available": False,
            "reason": "android.bridge_endpoint is null",
        }
        missing.append("android_bridge")

    # 5. Models (Image gen and VLM)
    models = data.get("models") or {}
    img_ep = models.get("image_gen_endpoint")
    vlm_ep = models.get("vlm_endpoint")
    if img_ep and vlm_ep:
        deps["models"] = {
            "available": True,
            "image_gen_endpoint": img_ep,
            "vlm_endpoint": vlm_ep,
        }
    else:
        deps["models"] = {
            "available": False,
            "reason": f"image_gen_endpoint ({img_ep}) or vlm_endpoint ({vlm_ep}) is null",
        }
        missing.append("models")

    # 6. Skill folder name
    folder_name = data.get("skill_folder_name")
    if folder_name in ("osNextLife", "ocNextLife"):
        deps["skill_folder_name"] = {"available": True, "value": folder_name}
    else:
        deps["skill_folder_name"] = {
            "available": False,
            "reason": f"skill_folder_name is null or invalid ({folder_name})",
        }
        missing.append("skill_folder_name")

    exit_code = 0 if len(missing) == 0 else 10
    result = {
        "available": (exit_code == 0),
        "missing_dependencies": missing,
        "dependencies": deps,
    }
    return result, exit_code


def main() -> None:
    res, code = check_preflight()
    sys.stdout.write(json.dumps(res, ensure_ascii=False, indent=2) + "\n")
    sys.stdout.flush()
    sys.exit(code)


if __name__ == "__main__":
    main()
