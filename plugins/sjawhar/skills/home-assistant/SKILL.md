---
name: home-assistant
description: Use when accessing, configuring, debugging or optimizing Home Assistant or its devices, entity states, automations, scripts, scenes, dashboards, helpers, areas, HACS or add-ons. Triggers on Home Assistant, home automation, smart home, lights, thermostat, sensors, automations, Lovelace.
mcp:
  home-assistant:
    command: secrets
    args: ["HA_MCP_URL", "--", "npx", "-y", "-p", "mcp-remote@0.14.3", "mcp-remote-from-env", "HA_MCP_URL", "--allow-http"]
---

# Home Assistant

Read and write access through [ha-mcp](https://github.com/homeassistant-ai/ha-mcp), MCP server `home-assistant`.

- omp loads the server only in a session whose working directory holds a `.omp/mcp.json` that declares it (it does not look in parent directories). There, call a tool with the `write` tool: write its JSON arguments as the content of the path `xd://mcp__home_assistant_<tool>`. Read that path first for the tool's argument schema.
- With a `skill_mcp` tool: `skill_mcp(mcp_name="home-assistant", ...)`.
- Anywhere else, and for batch calls: [ha-mcp-call.sh](ha-mcp-call.sh), with the commands below. It prints each text content and exits 0. On a curl failure (such as a 401 refusal), a JSON-RPC error, a result with `isError` true, or no answer, it exits non-zero and prints the error on stderr.

## Instances

The keys below are agent tier: `secrets <KEY> -- <command>` gives them to an agent with no YubiKey touch and no approval (a human-tier key needs one of those).

- Salt Lake: `HA_MCP_URL` is its ha-mcp webhook URL on its Nabu Casa remote address, so it works from any network; only the secret holds that address. The webhook id is the credential. `dojo.thecybermonk.com` is not this webhook's host: that name is for Sami's primary instance, which moves to the SF Green. The same id at `http://10.0.51.23:8123/api/webhook/<id>` works only on that house's LAN. Call: `secrets HA_MCP_URL -- ~/.dotfiles/plugins/sjawhar/skills/home-assistant/ha-mcp-call.sh <tool> '<json>'`.
- SF: a Home Assistant Green at `10.0.0.254` on the apartment LAN. That address is static, set on the Green, and outside the gateway's assumed DHCP pool; the gateway reserves nothing for it (`skills/majordomo/references/sites/wiz.md` in sjawhar/dojo). `HA_MCP_URL_BUSH` is its webhook URL `http://10.0.0.254:8123/api/webhook/<id>`; the id is a secret. It works only from that LAN (oryx is on it). Its webhook is in auth mode (below), so each call also sends `HA_TOKEN_BUSH`, the Wren user's long-lived token; without it the webhook answers 401. There is no `HA_MCP_TOKEN_BUSH` secret; the `bash -c` maps the names:
  - Call: `secrets HA_MCP_URL_BUSH HA_TOKEN_BUSH -- bash -c 'export HA_MCP_URL="$HA_MCP_URL_BUSH" HA_MCP_TOKEN="$HA_TOKEN_BUSH"; exec ~/.dotfiles/plugins/sjawhar/skills/home-assistant/ha-mcp-call.sh "$@"' _ <tool> '<json>'`
  - MCP client: `secrets HA_MCP_URL_BUSH HA_TOKEN_BUSH -- bash -c 'export HA_MCP_TOKEN_BUSH="$HA_TOKEN_BUSH"; unset HA_TOKEN_BUSH; exec npx -y -p mcp-remote@0.14.3 mcp-remote-from-env HA_MCP_URL_BUSH --allow-http'`
- The stopped container `homeassistant-sf` on oryx is a rollback copy, not the SF instance. Do not start it.

## Credentials and auth mode

- Do not expand `$HA_MCP_URL` or a token into a command's arguments (`curl "$HA_MCP_URL"`, `mcp-remote "$HA_MCP_URL"`, `-H "Authorization: Bearer $TOKEN"`): every user can read `/proc/<pid>/cmdline`, and any agent's `ps` or `pgrep -af` prints the id into its transcript. Pass them through the environment, a config file or stdin.
- The MCP launch in this skill's frontmatter runs `~/.dotfiles/scripts/mcp-remote-from-env`. It takes the URL from `HA_MCP_URL`, unsets it, runs `mcp-remote` 0.14.3 in its own process, and redacts the URL from stderr.
- Auth mode: a webhook whose HA-MCP Server option `webhook_auth` is `ha_auth` ("Sign in with Home Assistant") answers 401 without a long-lived HA token. Salt Lake's token is `HA_TOKEN`. Read [references/auth-mode.md](references/auth-mode.md) when a call answers 401, or before you call or launch a client for an auth-mode webhook other than with the SF commands above.
- ha-mcp runs inside HA: HACS component "HA-MCP Custom Component", entry "HA-MCP Server". The admin-only HA-MCP panel in the HA sidebar turns tools on and off and sets Read Only Mode, feature flags and edit backups.
- File and YAML tools (`ha_read_file`, `ha_config_set_yaml`, ...) are beta and off. They need the "HA-MCP File & YAML Tools" entry and its feature flags.

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

Read [references/tool-quirks.md](references/tool-quirks.md) before you create or update an automation, install an update, or call `ha_set_entity`, `ha_remove_helpers_integrations`, `ha_manage_backup`, `ha_manage_hacs`, `ha_get_history`, `ha_manage_energy_prefs` or `ha_restart`. It gives the arguments each needs and what each answer means.

## Known behavior

- Matter commissioning (pairing) through MCP fails: the transport timeout aborts it. Pair in the HA web UI or companion app.
- Device-trigger automations (registry UUIDs) warn "not found in entity registry" on every write. Ignore it on a config that predates your edit.
- Renames do not reach energy prefs. Update `ha_manage_energy_prefs` explicitly.
- New entities on a device with an area get the area in their entity_id (`sensor.bedroom_2_office_equipment_…`), attached helpers too. Expect to rename.
- Give a device's entities one name prefix (`kitchen_outlet_`), or card auto-discovery breaks.
- An automation that turns a device on must not override a human's off. Context `parent_id` none marks a human action; store it in a hold helper (`input_boolean.office_ac_hold`) and check it first.
- Fans spoof mmWave occupancy. In an automation that acts on occupancy, require PIR motion as well as mmWave.
- Read [references/matter-and-tapo.md](references/matter-and-tapo.md) when you work on Matter or Tapo devices or their firmware, or need a UI-only action that has no tool or service.
