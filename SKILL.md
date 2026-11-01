---
name: nextlife
description: OpenClaw skill for buying and selling second-hand goods via marketplaces.
commands:
  - nextlife status
  - nextlife item create --title <title> --category <category>
  - nextlife item show --code <code>
  - nextlife research start --code <code>
  - nextlife research report --code <code>
  - nextlife media ingest --code <code> --files <paths...>
  - nextlife media build --code <code>
  - nextlife draft build --code <code>
  - nextlife publish --code <code>
  - nextlife publish status --code <code>
  - nextlife outbox pull
  - nextlife outbox ack --id <id> --delivery-evidence <json>
  - nextlife inbox push
  - nextlife inbox pending
  - nextlife deal close --code <code>
  - nextlife canaries run
  - nextlife canaries status
  - nextlife stock list [--group all|on_sale|drafts|sold] [--q <text>]
  - nextlife stock show --code <code>
  - nextlife stock create --title <title> [--set <field>=<value> ...]
  - nextlife stock update --code <code> --set <field>=<value> [--set ...]
  - nextlife stock sold --code <code>
  - nextlife stock delete --code <code> [--confirm] [--confirm-listings]
  - nextlife stock photos --code <code>
  - nextlife stock photo-delete --code <code> --photo-id <id>
exit_codes:
  0: Success
  2: Input error
  3: External system unavailable
  4: Owner decision required
  5: Internal error
---

# NextLife Skill for OpenClaw

## Description
NextLife automates selling and buying second-hand items across multiple
European marketplaces (Bazoš, Sbazar, Aukro, etc.). The CLI is a thin HTTP
client to the NextLife service on 127.0.0.1 (default port 8081),
authenticated with `NEXTLIFE_OPENCLAW_TOKEN` (injected by the skill runtime;
never ask the owner for the token value, never print it).

## Output Contract
- All CLI output to `stdout` is strictly JSON.
- Diagnostic logs go to `stderr`.
- Exit codes strictly adhere to the specification.

## Draft and approval workflow
- `nextlife draft build --code <code>` returns the draft and a `web_url`
  (e.g. `/ui/draft/ABC`). **Send that link to the owner** — approval is an
  owner action in the web UI only; the CLI cannot approve.
- `nextlife publish --code <code>` requires a current approval: if the draft
  is not approved or was modified after approval, the API returns 409 and the
  CLI exits with code 4. Then send the owner the `web_url` again and ask to
  approve the current draft before retrying.

## Склад (Warehouse)
The «Склад» is the catalogue of sale items. Every item is a row with a code
(`#NNN`) and editable fields; the listing wizard works on top of a Склад item.
All Склад operations are available via the `nextlife stock ...` commands
(thin HTTP client to `/api/oc/stock/*`).

Rules for the OpenClaw agent (ТЗ «Склад» §5.3):
1. Before deleting, run `nextlife stock delete --code <code>` WITHOUT flags.
   It exits 4 (HTTP 409) with a `delete_preview`. Show the owner the item name,
   code, photo count and the listing list.
2. Get a separate «да» from the owner for deleting the item AND a separate «да»
   for taking the listings down. Only then re-run with
   `--confirm --confirm-listings`.
3. After `stock sold` and `stock delete`, tell the owner every listing with
   status `failed` and its link for manual takedown.
4. In any dispatch/shipping action, name the owner the storage location
   (`storage_location`).
5. Never include operator notes (`notes`) in buyer-facing text.

Field names in `--set field=value` match the item columns (e.g. `title`,
`description`, `description_lang`, `usage`, `specs`, `storage_location`,
`target_price`, `target_currency`, `weight_g`, `packed_weight_g`,
`dim_l_mm`, `dim_w_mm`, `dim_h_mm`, `shipping_policy`, `notes`).
