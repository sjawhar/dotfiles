---
name: home-assistant
description: Use when accessing, configuring, or optimizing Home Assistant, such as controlling devices or querying entity states. Also for creating or debugging automations/scripts/scenes and managing dashboards, helpers, areas, HACS, or add-ons. Triggers on Home Assistant, home automation, smart home, lights, thermostat, sensors, automations, Lovelace.
mcp:
  home-assistant:
    command: secrets
    args: ["HA_MCP_URL", "--", "npx", "-y", "-p", "mcp-remote@0.14.3", "mcp-remote-from-env", "HA_MCP_URL", "--allow-http"]
---

# Home Assistant

Read/write access through [ha-mcp](https://github.com/homeassistant-ai/ha-mcp) (78 tools on HA 2026.8), MCP server `home-assistant`. omp loads that server only in a session whose working directory holds a `.omp/mcp.json` that declares it (omp does not look in parent directories); there, write JSON args to `xd://mcp__home_assistant_<tool>`. Anywhere else, use `ha-mcp-call.sh` (Access, below). With `skill_mcp`, call `skill_mcp(mcp_name="home-assistant", ...)`.

## Access

- ha-mcp runs inside HA: HACS component "HA-MCP Custom Component", entry "HA-MCP Server". The admin-only **HA-MCP panel** in the HA sidebar sets tool enable/disable, Read Only Mode, feature flags and edit backups.
- The agent-tier secret `HA_MCP_URL` is a webhook URL, and its id is the credential. It holds the remote form `https://dojo.thecybermonk.com/api/webhook/<id>`, which works from any network. The same id at the LAN address `http://10.0.51.23:8123/api/webhook/<id>` works only on the house's LAN.
- The SF Home Assistant on oryx (container `homeassistant-sf`) is a separate instance. Its key, the agent-tier `HA_MCP_URL_BUSH`, is the loopback endpoint `http://127.0.0.1:9584/<secret path>`, so it works only on oryx: its ha-mcp webhook is off. Call it with `secrets HA_MCP_URL_BUSH -- bash -c 'export HA_MCP_URL="$HA_MCP_URL_BUSH"; ~/.dotfiles/plugins/sjawhar/skills/home-assistant/ha-mcp-call.sh <tool> "<json>"'`.
- The MCP launch runs `~/.dotfiles/scripts/mcp-remote-from-env`. It takes the URL from `HA_MCP_URL`, unsets it, runs `mcp-remote` 0.14.3 in-process, and redacts the URL from stderr.
- Never expand `$HA_MCP_URL` or a token into command arguments (`curl "$HA_MCP_URL"`, `mcp-remote "$HA_MCP_URL"`, `-H "Authorization: Bearer $TOKEN"`): every user can read `/proc/<pid>/cmdline`, and any agent's `ps` or `pgrep -af` prints the id into its transcript. Pass it through the environment, a config file or stdin.
- Batch or scripted calls: `secrets HA_MCP_URL -- ha-mcp-call.sh <tool> '<json>'` ([ha-mcp-call.sh](ha-mcp-call.sh) gives curl the URL with `--config`).
- File/YAML tools (`ha_read_file`, `ha_config_set_yaml`, ...) are beta and not enabled. They need the "HA-MCP File & YAML Tools" entry and feature flags.

### Webhook in "Sign in with Home Assistant" mode

An ha-mcp webhook whose auth mode (HA-MCP Server options, `webhook_auth: ha_auth`) is "Sign in with Home Assistant" answers 401 unless the call carries an HA access token as `Authorization: Bearer`. A long-lived access token works: the SF instance's is the agent-tier `HA_TOKEN_BUSH`, Salt Lake's `HA_TOKEN`. Both callers send it when the variable named next to the URL holds it, and send no Authorization header when it is unset or empty:

- `ha-mcp-call.sh` reads `HA_MCP_TOKEN` (and puts it in curl's `--config`, like the URL): `secrets HA_MCP_URL HA_TOKEN -- bash -c 'export HA_MCP_TOKEN="$HA_TOKEN"; ~/.dotfiles/plugins/sjawhar/skills/home-assistant/ha-mcp-call.sh <tool> "<json>"'`. A refusal prints `curl: (22) The requested URL returned error: 401` and exits non-zero.
- `mcp-remote-from-env <URL_NAME>` reads the name with its `URL` part replaced by `TOKEN` (`HA_MCP_URL` → `HA_MCP_TOKEN`, `HA_MCP_URL_BUSH` → `HA_MCP_TOKEN_BUSH`), unsets it, hands mcp-remote `--header "Authorization: Bearer <token>"` in-process, and redacts the token from stderr. For an auth-mode server, the launch becomes `secrets HA_MCP_URL HA_TOKEN -- bash -c 'export HA_MCP_TOKEN="$HA_TOKEN"; unset HA_TOKEN; exec npx -y -p mcp-remote@0.14.3 mcp-remote-from-env HA_MCP_URL --allow-http'`.

`secrets` exports each key under its own name and cannot rename it, so the `bash -c` maps the token key to the name the caller reads.

## Work

1. Orient: `ha_get_overview` (summary), `ha_search` (fuzzy entity/config lookup), `ha_get_state` (live states).
2. Before you write automations, scripts or helpers, read and follow the bundled guides. `ha_get_skill_guide` with no args lists them, and `skill` + `file` reads one.
3. Config writes (`ha_config_set_automation/_script/_scene/_helper/_dashboard`) fail without the current BestPracticeKey. Read it from `ha_get_skill_guide(skill='home-assistant-best-practices', file='references/automation-patterns.md')`. It rotates hourly, so read it again each session and each hour. Pass `MandatoryBPS=false` to skip receiving the reference content again.
4. Validate Jinja2 with `ha_eval_template` before you put it in a config.
5. Debug automations: `ha_get_automation_traces`, `ha_get_history`, `ha_get_logs`.

## Safety

- The server backs up config edits. Still, get the human's confirmation before deletes (`ha_config_remove_*`, `ha_remove_*`), `ha_restart`, and `ha_call_service` on locks, garage doors, climate entities and valves.
- When you audit, call only the read tools (`readOnlyHint` in the tool annotations), so an audit changes nothing.
- `ha_get_app(slug=...)` returns all add-on `options`, secrets included (Advanced SSH `ssh.password`). Select fields with `jq` (`.addon.options.ssh.authorized_keys`). Never print the raw options.
- `ha_manage_app` config mode merges one nested level: `{"ssh": {"authorized_keys": [...]}}` keeps the other `ssh.*` fields.

## Tool quirks

| Tool | Quirk |
|---|---|
| automations | Omit `identifier` to create. An update needs `identifier` + `config.id`. |
| `ha_set_entity` | Sets `new_entity_id`, `name`, `enabled`, `area_id`. `name` replaces the WHOLE friendly name, even with `has_entity_name`. Pass "Kitchen Outlet Energy", not "Energy", or two devices' sensors look alike. |
| `ha_remove_helpers_integrations` | Entry: `target` = entry_id. Helper: `target` + `helper_type`. Always `confirm: true`. |
| `ha_manage_backup` | `scope: "snapshot"`. Snapshot delete needs a human-set server flag, and refuses backups with unprovable provenance. |
| `ha_manage_hacs` | Only `add_repository`/`download`. Uninstall in the UI. |
| `ha_get_history` | `entity_ids` list. States without attributes, ~24h, 100-point cap. |
| `ha_manage_energy_prefs` | Needs `mode` + fresh `config_hash`. New `total`/`total_increasing` sensors give `statistics_not_defined` until the recorder's first statistics (~5 min). Harmless. |
| updates | Install with `ha_call_service` `update.install`, not `ha_manage_updates`. |
| `ha_restart` | The transport times out when HA goes down (expected). Poll the base URL for HTTP 200 (~60-90s). |

## Known behavior

- Matter commissioning (pairing) through MCP fails, because the transport timeout aborts it. Always pair in the HA web UI or companion app.
- Device-trigger automations (registry UUIDs) warn "not found in entity registry" on every write. Ignore it on a config that predates your edit.
- Renames don't reach energy prefs. Update `ha_manage_energy_prefs` explicitly.
- New entities on a device with an area get the area in their entity_id (`sensor.bedroom_2_office_equipment_…`), attached helpers too. Expect to rename.
- Give a device's entities one name prefix (`kitchen_outlet_`), or card auto-discovery breaks.
- An automation that turns a device on must not override a human's off. Context `parent_id` none marks a human action; store it in a hold helper (`input_boolean.office_ac_hold`) and check it first.
- Fans spoof mmWave occupancy. In an automation that acts on occupancy, require PIR motion as well as mmWave.
- Matter or Tapo devices, firmware, or a UI-only action with no tool or service: read [matter-and-tapo.md](matter-and-tapo.md).
