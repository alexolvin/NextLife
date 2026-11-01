"""CLI entrypoint for NextLife. Single entry point for OpenClaw.

Item 3: thin HTTP client. Reads ONLY NEXTLIFE_BASE_URL (loopback default,
see http_client.DEFAULT_BASE_URL) and NEXTLIFE_OPENCLAW_TOKEN. No database
access. A missing token fails BEFORE any network call (exit 2, var named).
"""

import json
import sys
from typing import Any

import httpx

from nextlife.cli import http_client
from nextlife.cli.commands import (
    handle_canaries_cmd,
    handle_deal_cmd,
    handle_draft_cmd,
    handle_inbox_cmd,
    handle_item_cmd,
    handle_media_cmd,
    handle_outbox_cmd,
    handle_publish_cmd,
    handle_research_cmd,
    handle_search_cmd,
    handle_status_cmd,
    handle_stock_cmd,
)
from nextlife.core.exceptions import InputError
from nextlife.obs.logger import setup_logger

logger = setup_logger("nextlife.cli")

EXIT_SUCCESS = 0
EXIT_INPUT_ERROR = 2
EXIT_EXTERNAL_ERROR = 3
EXIT_OWNER_REQUIRED = 4
EXIT_INTERNAL_ERROR = 5


def output_json(data: dict[str, Any]) -> None:
    """Print strictly JSON to stdout without any additional text."""
    sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    sys.stdout.flush()


def parse_named_args(args: list[str]) -> tuple[list[str], dict[str, Any]]:
    """Parse positional args and --key [value...] flags.

    A flag repeated on the command line (e.g. ``--set a=1 --set b=2``) is
    accumulated into a list; a single occurrence keeps its scalar/list value.
    """
    positionals: list[str] = []
    flags: dict[str, Any] = {}
    i = 0
    while i < len(args):
        arg = args[i]
        if arg.startswith("--"):
            key = arg[2:].replace("-", "_")
            values: list[str] = []
            j = i + 1
            while j < len(args) and not args[j].startswith("--"):
                values.append(args[j])
                j += 1
            if not values:
                new_val: Any = True
                i += 1
            elif len(values) == 1:
                new_val = values[0]
                i = j
            else:
                new_val = values
                i = j
            if key in flags:
                existing = flags[key]
                added = new_val if isinstance(new_val, list) else [new_val]
                flags[key] = (existing if isinstance(existing, list) else [existing]) + added
            else:
                flags[key] = new_val
        else:
            positionals.append(arg)
            i += 1
    return positionals, flags


def _api_error_exit_code(status_code: int) -> int:
    if status_code == 409:
        return EXIT_OWNER_REQUIRED
    if status_code in (400, 401, 404, 422):
        return EXIT_INPUT_ERROR
    return EXIT_INTERNAL_ERROR


def handle_cli(args: list[str]) -> int:
    if not args or args[0] in ("-h", "--help", "help"):
        output_json(
            {
                "skill": "nextlife",
                "available_commands": [
                    "status",
                    "item create",
                    "item show",
                    "research start",
                    "research report",
                    "media ingest",
                    "media build",
                    "draft build",
                    "publish",
                    "publish status",
                    "outbox pull",
                    "outbox ack",
                    "inbox push",
                    "inbox pending",
                    "deal close",
                    "canaries run",
                    "canaries status",
                    "search",
                ],
            }
        )
        return EXIT_SUCCESS

    # Fail-fast BEFORE any network call: the token must be in the process env.
    try:
        token = http_client.get_token()
    except http_client.CliTokenMissingError as exc:
        logger.error(str(exc))
        output_json({"error": str(exc), "code": EXIT_INPUT_ERROR})
        return EXIT_INPUT_ERROR

    positionals, flags = parse_named_args(args)
    cmd = positionals[0] if positionals else ""

    try:
        if cmd == "status":
            output_json(handle_status_cmd(token))
            return EXIT_SUCCESS

        if cmd == "item":
            output_json(handle_item_cmd(flags, positionals, token))
            return EXIT_SUCCESS

        if cmd == "research":
            output_json(handle_research_cmd(flags, positionals, token))
            return EXIT_SUCCESS

        if cmd == "media":
            output_json(handle_media_cmd(flags, positionals, token))
            return EXIT_SUCCESS

        if cmd == "draft":
            output_json(handle_draft_cmd(flags, positionals, token))
            return EXIT_SUCCESS

        if cmd == "publish":
            output_json(handle_publish_cmd(flags, positionals, token))
            return EXIT_SUCCESS

        if cmd == "outbox":
            output_json(handle_outbox_cmd(flags, positionals, token))
            return EXIT_SUCCESS

        if cmd == "inbox":
            output_json(handle_inbox_cmd(flags, positionals, token))
            return EXIT_SUCCESS

        if cmd == "deal":
            output_json(handle_deal_cmd(flags, positionals, token))
            return EXIT_SUCCESS

        if cmd == "canaries":
            output_json(handle_canaries_cmd(flags, positionals, token))
            return EXIT_SUCCESS

        if cmd == "search":
            output_json(handle_search_cmd(flags, positionals, token))
            return EXIT_SUCCESS

        if cmd == "stock":
            output_json(handle_stock_cmd(flags, positionals, token))
            return EXIT_SUCCESS

        raise InputError(f"Unknown command: {cmd}")

    except http_client.ApiError as exc:
        code = _api_error_exit_code(exc.status_code)
        if code == EXIT_OWNER_REQUIRED:
            logger.info(f"Owner decision required: {exc.detail}")
        else:
            logger.error(f"API error {exc.status_code}: {exc.detail}")
        output_json({"error": exc.detail, "code": code})
        return code
    except httpx.TransportError as exc:
        logger.error(f"Cannot reach the NextLife API at {http_client.base_url()}: {exc}")
        output_json(
            {
                "error": f"Cannot reach the NextLife API at {http_client.base_url()}: {exc}",
                "code": EXIT_EXTERNAL_ERROR,
            }
        )
        return EXIT_EXTERNAL_ERROR
    except InputError as exc:
        logger.error(f"Input error: {exc}")
        output_json({"error": str(exc), "code": EXIT_INPUT_ERROR})
        return EXIT_INPUT_ERROR
    except Exception as exc:
        logger.error(f"Internal error: {exc}")
        output_json({"error": str(exc), "code": EXIT_INTERNAL_ERROR})
        return EXIT_INTERNAL_ERROR


def entrypoint() -> None:
    code = handle_cli(sys.argv[1:])
    sys.exit(code)


if __name__ == "__main__":
    entrypoint()
