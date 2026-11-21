---
name: home-assistant
description: Use when controlling or querying Home Assistant devices and entity states, or when creating or debugging its automations, scripts, scenes, dashboards, helpers, areas, HACS or add-ons. Triggers on Home Assistant, home automation, smart home, lights, thermostat, sensors, Lovelace.
mcp:
  home-assistant:
    command: secrets
    args: ["HA_MCP_URL", "--", "npx", "-y", "-p", "mcp-remote@0.14.3", "mcp-remote-from-env", "HA_MCP_URL", "--allow-http"]
---

# Home Assistant

Read and write access through [ha-mcp](https://github.com/homeassistant-ai/ha-mcp), MCP server `home-assistant`.

- omp loads the server only in a session whose working directory holds a `.omp/mcp.json` that declares it (it does not look in parent directories). There, write JSON args to `xd://mcp__home_assistant_<tool>`.
- With a `skill_mcp` tool: `skill_mcp(mcp_name="home-assistant", ...)`.
- Anywhere else, and for batch calls: [ha-mcp-call.sh](ha-mcp-call.sh), with the commands below.

## Instances

- Salt Lake: the agent-tier `HA_MCP_URL` is its ha-mcp webhook URL on its Nabu Casa remote address, so it works from any network. The webhook id is the credential. The same id at `http://10.0.51.23:8123/api/webhook/<id>` works only on that house's LAN. Call: `secrets HA_MCP_URL -- ~/.dotfiles/plugins/sjawhar/skills/home-assistant/ha-mcp-call.sh <tool> '<json>'`.
- SF: a Home Assistant Green at `10.0.0.254` on the apartment LAN. The agent-tier `HA_MCP_URL_BUSH` is its webhook URL and works only from that LAN (oryx is on it). Its webhook is in auth mode, so each call also sends the agent-tier `HA_TOKEN_BUSH`. There is no `HA_MCP_TOKEN_BUSH` secret; the `bash -c` maps the names:
  - Call: `secrets HA_MCP_URL_BUSH HA_TOKEN_BUSH -- bash -c 'export HA_MCP_URL="$HA_MCP_URL_BUSH" HA_MCP_TOKEN="$HA_TOKEN_BUSH"; exec ~/.dotfiles/plugins/sjawhar/skills/home-assistant/ha-mcp-call.sh "$@"' _ <tool> '<json>'`
  - MCP client: `secrets HA_MCP_URL_BUSH HA_TOKEN_BUSH -- bash -c 'export HA_MCP_TOKEN_BUSH="$HA_TOKEN_BUSH"; unset HA_TOKEN_BUSH; exec npx -y -p mcp-remote@0.14.3 mcp-remote-from-env HA_MCP_URL_BUSH --allow-http'`
- The stopped container `homeassistant-sf` on oryx is a rollback copy, not the SF instance. Do not start it.

## Auth mode and credentials

- A webhook whose HA-MCP Server option `webhook_auth` is `ha_auth` ("Sign in with Home Assistant") answers 401 without a long-lived HA token. Which variable each caller reads is in the header of `ha-mcp-call.sh` and `scripts/mcp-remote-from-env`; map a token key to it in `bash -c`, as the SF commands do. Salt Lake's token is `HA_TOKEN`.
- Pass the URL and tokens only through those two callers, not in a command's arguments, where `ps` shows them to every user.
- ha-mcp runs inside HA: HACS component "HA-MCP Custom Component", entry "HA-MCP Server". The admin-only HA-MCP panel in the HA sidebar turns tools on and off and sets Read Only Mode, feature flags and edit backups.
- File and YAML tools (`ha_read_file`, `ha_config_set_yaml`, ...) are beta and off. They need the "HA-MCP File & YAML Tools" entry and its feature flags.

## The SF Green's lockout

- Since 2026-11-20 the Green bans an address after 5 requests with invalid authentication (a wrong, expired or revoked token, or a failed login): Home Assistant then answers 403 to every request from that address on port 8123, ha-mcp included, until the address leaves `ip_bans.yaml` and Home Assistant restarts. SSH and the apps on their own ports still answer. The count is per address, and a request with a valid token never resets it; only a login through the login page, a websocket login from that address, or a restart does. Send the Green only a token you have seen work, and test refusals against a stub. Remote UI requests count against the client's public address.
- The threshold is in the http settings (Settings → System → Network), not YAML: websocket `http/config/configure` restarts Home Assistant on new settings, and `http/config/promote` within 5 minutes keeps them, else it restarts on the old ones. Remove a ban, then restart (automations pause about two minutes):

  ```sh
  ssh -i ~/.ssh/ha-dojo root@10.0.0.254 'a=<address>; f=/homeassistant/ip_bans.yaml; cp -p "$f" "$f.bak" && sed -i "/^${a//./\\.}:\$/,+1d" "$f" && cat "$f"'
  ssh -i ~/.ssh/ha-dojo root@10.0.0.254 'ha core restart'
  ```

## Work

1. Orient: `ha_get_overview` (summary), `ha_search` (fuzzy entity and config lookup), `ha_get_state` (live states).
2. Before you write automations, scripts or helpers, read and follow the bundled guide: `ha_get_skill_guide` with no arguments returns its `SKILL.md` and the list of its reference files; pass `file` to read one.
3. Config writes (`ha_config_set_automation/_script/_scene/_helper/_dashboard`) fail without the current BestPracticeKey, which that no-argument answer holds. It rotates hourly: read it again each session and each hour. Pass `MandatoryBPS=false` to skip receiving the reference content again.
4. Validate Jinja2 with `ha_eval_template` before you put it in a config.
5. Debug automations: `ha_get_automation_traces`, `ha_get_history`, `ha_get_logs`.

## Safety

- The server backs up config edits. Still, get the human's confirmation before deletes (`ha_config_remove_*`, `ha_remove_*`), `ha_restart`, and `ha_call_service` on locks, garage doors, climate entities and valves.
- When you audit, call only the read tools (`readOnlyHint` in the tool annotations).
- `ha_get_app(slug=...)` returns all add-on `options`, secrets included (Advanced SSH `ssh.password`). Select fields with `jq` (`.addon.options.ssh.authorized_keys`); do not print the raw options.
- `ha_manage_app` config mode merges one nested level: `{"ssh": {"authorized_keys": [...]}}` keeps the other `ssh.*` fields.

## Tool quirks

- Automations: omit `identifier` to create. An update needs `identifier` and `config.id`.
- `ha_set_entity` sets `new_entity_id`, `name`, `enabled`, `area_id`. `name` replaces the whole friendly name, even with `has_entity_name`: pass "Kitchen Outlet Energy", not "Energy", or two devices' sensors look alike.
- `ha_remove_helpers_integrations`: an entry takes `target` = entry_id; a helper takes `target` and `helper_type`. Pass `confirm: true`.
- `ha_manage_backup`: `scope: "snapshot"`. Deleting a snapshot needs a human-set server flag, and it refuses backups with unprovable provenance.
- `ha_manage_hacs` does only `add_repository` and `download`. Uninstall in the UI.
- `ha_get_history` takes an `entity_ids` list and returns states without attributes, about 24h, at most 100 points.
- `ha_manage_energy_prefs` needs `mode` and a fresh `config_hash`. New `total`/`total_increasing` sensors give `statistics_not_defined` until the recorder's first statistics (about 5 min).
- Install updates with `ha_call_service` `update.install`, not `ha_manage_updates`.
- `ha_restart`: the transport times out when HA goes down. Poll the base URL for HTTP 200 (about 60-90s).

## Known behavior

- Matter commissioning (pairing) through MCP fails: the transport timeout aborts it. Pair in the HA web UI or companion app.
- Device-trigger automations (registry UUIDs) warn "not found in entity registry" on every write. Ignore it on a config that predates your edit.
- Renames do not reach energy prefs. Update `ha_manage_energy_prefs` explicitly.
- New entities on a device with an area get the area in their entity_id (`sensor.bedroom_2_office_equipment_…`), attached helpers too. Expect to rename.
- Give a device's entities one name prefix (`kitchen_outlet_`), or card auto-discovery breaks.
- An automation that turns a device on must not override a human's off. Context `parent_id` none marks a human action; store it in a hold helper (`input_boolean.office_ac_hold`) and check it first.
- Fans spoof mmWave occupancy. In an automation that acts on occupancy, require PIR motion as well as mmWave.
- Read [references/matter-and-tapo.md](references/matter-and-tapo.md) when you work on Matter or Tapo devices or their firmware, or need a UI-only action that has no tool or service.
