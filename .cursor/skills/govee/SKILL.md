---
name: govee
description: >-
  Govee smart lights via MCP and Cuttle — API key, device inventory, cloud v1/v2,
  scenes, schedules, screen sync, zone groups. Use when the user mentions Govee,
  smart LEDs, home lighting, scenes, or automating lights from Cuttle/Cursor.
---

# Govee lighting (Cuttle + Cursor)

## Device inventory (local, not in git)

Device IDs and zones live in **`src/data/home_automation_devices.json`** (gitignored).  
Copy from [`home_automation_devices.example.json`](vscode://file//path/to/Cuttle/src/data/home_automation_devices.example.json) and fill in real devices from Govee / `list_devices`.

Personal named snapshots (Firelit, Cinematic, …) may live under `_personal/docs/govee-presets.md` on this machine.

## Room groups

Map user words → **zone** field on each device row (`bedroom`, `living`, …). Control every device in that zone unless they name a single light.

| User says (examples) | Typical zone |
|----------------------|--------------|
| bedroom, bed room | `bedroom` |
| living room, lounge, PC lights, desk | `living` |
| all lights | every row in the inventory |

If ambiguous, ask which zone. Prefer **`list_devices`** when the inventory file is empty or stale.

## Credentials

- Put **`GOVEE_API_KEY`** in `src/.env` (gitignored). Obtain from **Govee Home → Settings → About → Apply for API key**.
- Cursor’s **`govee`** MCP server uses `envFile` → `src/.env` in `.cursor/mcp.json`.
- Home Automation UI: `/home_automation.html` — works when the key is set; not part of OOBE.

### Cursor `mcp-govee` — LAN (optional)

- Many bulbs (H6003/H6008) use **cloud API only**.
- LAN-capable models: enable LAN in Govee Home, then `GOVEE_LAN_ENABLED=true` (see vendor README).

## Two tool surfaces

| Context | Server / tools |
|--------|----------------|
| **Cursor `govee` MCP** | `vendor/mcp-govee` — `list_devices`, `set_power`, … |
| **Cuttle MCP** | `govee_list_devices`, `govee_set_power`, … |

## Control pattern

1. Resolve targets from inventory zones or `list_devices`.
2. Every call needs `device_id` + `model`.
3. Prefer built-in **scenes** over tight brightness loops (v2 rate limits).

## Cuttle Home Automation

- Themes / schedule: `/home_automation.html` and `/api/home-automation/*`
- Manager: `src/managers/home_automation.py`
- Optional nav rail entry; no first-run wizard steps for Govee.
