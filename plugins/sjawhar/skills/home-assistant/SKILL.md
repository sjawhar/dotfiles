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
- **Beyond ha-mcp — HA WebSocket API with `HA_TOKEN` (Sep 7)**: some UI-only actions have no MCP tool or service but do have a websocket command. Known: `matter/interview_node` (device page "Re-interview"), `matter/ping_node`, `matter/node_diagnostics`, all taking `device_id`. Pattern: `secrets HA_TOKEN -- bun <script>` connecting to `ws://homeassistant.local:8123/api/websocket`, `auth` with `access_token`, then `{id, type, ...}`. There is no `matter.*` service for re-interview and the Matter Server's own port 5580 is not exposed on the LAN. `ha_get_integration(include_diagnostics=True, device_id=…)` returns the node attribute cache **as of the last interview**, not live subscription values — re-interview first if you need current attribute values.
- **Tapo P316M power strip (Sep 7 sweep + firmware update)**: on Tapo fw 1.0.5 (launch) it exposed six switch endpoints (1–6) plus six metering endpoints (7–12) with a broken energy cluster — `CumulativeEnergyImported` (`<ep>/145/1`) identical on every endpoint, the real per-socket kWh in `CumulativeEnergyExported` (`<ep>/145/2`), and no energy-cluster reports over the subscription (only re-interviews refreshed them). Tapo fw 1.4.1 (2026-06-23) fixes all of it: each socket is one endpoint (1–6) with plug + metering clusters, imported is per-socket, exported is 0, and energy reports live — use the native sensors, no Riemann helpers needed. Always confirm the endpoint→socket mapping from the metering endpoint's PowerTopology cluster (`<ep>/156/0` → `[socket]`); HA's generated names ("Power (7)") use endpoint numbers. Power/voltage/current report every ~10 s on both firmwares. New entities on a device that already has an area get the area prefixed into their entity_id (`sensor.bedroom_2_office_equipment_…`) — including helpers attached to that device — so expect to rename. Newly created `total`/`total_increasing` sensors trigger `statistics_not_defined` in `ha_manage_energy_prefs` post-save validation until the recorder compiles their first statistics (~5 min); harmless.
- **After a Matter firmware update changes a node's endpoint layout**: on re-interview the Matter integration drops every entity on the node and re-runs discovery, but its in-memory discovered-keys set survives, so entities whose discovery key existed before (switches, selects, update, identify) are NOT re-created — only the sensors whose endpoint numbers changed come back. Fix: `ha_call_service` `homeassistant.reload_config_entry` with `data.entry_id` = the Matter entry; the registry restores the original entity IDs and names for the re-created entities. Re-created sensors get fresh generated IDs and need renaming again.
- **Tapo firmware versions on Matter devices**: the Matter `SoftwareVersionString` (`0/40/10`, what HA's `update.*` entity shows) is NOT the Tapo firmware version — TP-Link leaves it at "1.3.0" across releases, and HA's "latest" comes from the Matter DCL, which lags TP-Link. On pre-1.4 firmware, Tapo local discovery reads the real version with no credentials: `uvx --from python-kasa kasa --host <ip> --debug discover` → `fw_ver`; 1.4.x switched the local protocol to `TPAP`, which python-kasa (as of Sep 2026) does not support, so after that the Tapo app is the only source. Compare against the model's TP-Link download page (`https://www.tp-link.com/us/support/download/<model>/` → "Firmware Release Note"). Updates only install via the Tapo app; expect an endpoint restructure and the reload dance above.
