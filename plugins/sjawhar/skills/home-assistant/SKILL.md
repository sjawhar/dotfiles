---
name: home-assistant
description: Use when accessing, configuring, or optimizing Home Assistant — controlling devices, querying entity states, creating or debugging automations/scripts/scenes, managing dashboards, helpers, areas, HACS, or add-ons. Triggers on Home Assistant, home automation, smart home, lights, thermostat, sensors, automations, Lovelace.
mcp:
  home-assistant:
    command: secrets
    args: ["HA_MCP_URL", "--", "bash", "-c", "exec npx -y mcp-remote \"$HA_MCP_URL\" --allow-http"]
---

# Home Assistant

Full read/write access to Home Assistant via [ha-mcp](https://github.com/homeassistant-ai/ha-mcp) (~87 tools). Use `skill_mcp(mcp_name="home-assistant", ...)` to invoke tools.

## Setup

The ha-mcp server runs **in-process inside Home Assistant** (HACS custom component "HA-MCP Custom Component" → "HA-MCP Server" entry). The connect URL embeds the credential and is stored as the agent-tier secret `HA_MCP_URL`; `npx mcp-remote` bridges stdio to the server's streamable-HTTP endpoint. An admin-only **HA-MCP panel** in the HA sidebar manages tool enable/disable, Read Only Mode, feature flags, and edit backups.

## Working effectively

- **Orient first**: `ha_get_overview` for a system summary, `ha_search` for fuzzy entity/config lookup, `ha_get_state` for live states.
- **Before writing automations/scripts/helpers**, call `ha_get_skill_guide` (no args lists bundled best-practice guides; pass `skill` + `file` to read one). These teach native constructs over Jinja2 workarounds, correct helper types, and automation modes — follow them.
- **Debugging automations**: `ha_get_automation_traces` for execution traces, `ha_get_history` for state history, `ha_get_logs` for system logs.
- **Templates**: validate Jinja2 with `ha_eval_template` before embedding in configs.

## Tool categories

| Category | Examples |
|----------|----------|
| Discovery | `ha_get_overview`, `ha_search`, `ha_get_state` |
| Control | `ha_call_service`, `ha_bulk_control`, `ha_list_services` |
| Automations/Scripts/Scenes | `ha_config_get/set/remove_automation`, `..._script`, `..._scene` |
| Dashboards | `ha_config_get/set_dashboard`, dashboard resources |
| Helpers/Areas/Zones/Labels | `ha_config_set_helper`, `ha_set_area_or_floor`, `ha_set_zone` |
| History/Debug | `ha_get_automation_traces`, `ha_get_history`, `ha_get_logs` |
| Registry | `ha_get_entity`, `ha_set_entity`, `ha_get_device` |
| System | `ha_manage_backup`, `ha_manage_updates`, `ha_manage_addon`, `ha_manage_hacs`, `ha_restart` |
| Media | `ha_get_camera_image`, `ha_get_dashboard_screenshot` (beta) |

File/YAML editing tools (`ha_read_file`, `ha_config_set_yaml`, ...) are beta and require the separate "HA-MCP File & YAML Tools" entry plus feature flags — not currently enabled.

## Safety

- Config edits are backed up automatically by the server, but treat deletes (`ha_config_remove_*`, `ha_remove_*`) and `ha_restart` as destructive: confirm with the human first.
- `ha_call_service` on real devices (locks, garage doors, climate, valves) has physical-world effects — same confirmation rule.
- Tool annotations include `readOnlyHint`/`destructiveHint`; prefer read tools when auditing.

## Operational notes (hard-won, Aug 2026)

- **Remote access**: `HA_MCP_URL` holds the remote webhook form (`https://dojo.thecybermonk.com/api/webhook/<id>`), which works from any network. For batch/scripted tool calls use [ha-mcp-call.sh](ha-mcp-call.sh): `secrets HA_MCP_URL -- ha-mcp-call.sh <tool> '<json>'`.
- **Gated writes need a BestPracticeKey**: config-writing tools (`ha_config_set_automation/_script/_scene/_helper/_dashboard`) reject calls until you read the current key from `ha_get_skill_guide(skill='home-assistant-best-practices', file='references/automation-patterns.md')`. The key rotates hourly — re-read it per session/hour. Pass `MandatoryBPS=false` to skip re-receiving the reference content.
- **Tool parameter quirks**: automations use `identifier` (updates need `identifier` + `config.id`; omit `identifier` to create). `ha_manage_backup` wants `scope: "snapshot"`; snapshot deletion is gated by a human-set server flag AND refuses backups with unprovable provenance. `ha_set_entity` does renames (`new_entity_id`), `name`, `enabled`, `area_id`. `ha_remove_helpers_integrations` deletes config entries (`target` = entry_id) and helpers (`target` + `helper_type`), always with `confirm: true`. `ha_manage_hacs` can only `add_repository`/`download` — removal is UI-only. `ha_get_history` takes `entity_ids` (list), returns states WITHOUT attributes, ~24h, 100-point cap. `ha_manage_energy_prefs` needs `mode` + fresh `config_hash`. Update installs: use `ha_call_service` `update.install` (not ha_manage_updates).
- **Restarts**: `ha_restart` times out at the transport when HA goes down — expected. Poll the base URL for HTTP 200 (~60-90s).
- **Matter commissioning through MCP fails** — the transport timeout aborts the in-flight commission. Always use the HA web UI or companion app for pairing.
- **Validator false positives**: automations using device-trigger format (registry UUIDs) produce "not found in entity registry" warnings on every write — harmless if the config predates you.
- **Design rules learned the hard way**: (1) never ship auto-ON without respecting manual-off (see `input_boolean.office_ac_hold` + context `parent_id is none` = human action); (2) mmWave occupancy sensors are spoofed by fans — corroborate with PIR motion for critical automations; (3) entity renames must be uniform per-device or card auto-discovery breaks.
- **Entity rename gotchas (Aug 18 Eve sweep)**: `ha_set_entity` `name` replaces the ENTIRE friendly name even when `has_entity_name` is true — pass "Kitchen Outlet Energy", not "Energy", or two devices' sensors become indistinguishable. Renames do NOT propagate to energy dashboard prefs (`ha_manage_energy_prefs`) — update them explicitly. Eve Energy 20ECN4101 dual outlets meter on Matter endpoint 1 (node device): one combined reading for both sockets; the "(top)" suffix HA generates is a naming artifact, not per-socket metering.
