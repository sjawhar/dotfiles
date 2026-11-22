---
name: home-assistant
description: Use when accessing, configuring, debugging or optimizing Home Assistant or its devices, entity states, automations, scripts, scenes, dashboards, helpers, areas, HACS or add-ons. Triggers on Home Assistant, home automation, smart home, lights, thermostat, sensors, automations, Lovelace.
mcp:
  home-assistant:
    command: secrets
    args: ["HA_MCP_URL", "--", "npx", "-y", "-p", "mcp-remote@0.14.3", "mcp-remote-from-env", "HA_MCP_URL", "--allow-http"]
---

# Home Assistant

Read and write access through ha-mcp, MCP server `home-assistant`.

| Situation | Call a tool |
| --- | --- |
| omp loaded the server | The two steps below |
| A `skill_mcp` tool exists | `skill_mcp(mcp_name="home-assistant", ...)` |
| Anywhere else, and for batch calls | [ha-mcp-call.sh](ha-mcp-call.sh), with the commands below |

omp loads the server only in a session whose working directory holds a `.omp/mcp.json` that declares it (it does not look in parent directories).

1. Read the path `xd://mcp__home_assistant_<tool>` for the tool's argument schema.
2. Write the tool's JSON arguments with the `write` tool as the content of that path.

ha-mcp-call.sh prints each text content and exits 0. It exits non-zero and prints the error on stderr on a curl failure (such as a 401 refusal), a JSON-RPC error, a result with `isError` true, or no answer.

## Instances

- The keys below are agent tier. `secrets <KEY> -- <command>` gives them to an agent with no YubiKey touch and no approval. A human-tier key needs one of those.

| Fact | Salt Lake | SF |
| --- | --- | --- |
| Host | Its Nabu Casa remote address, which only the secret holds | A Home Assistant Green at `10.0.0.254` on the apartment LAN |
| URL key | `HA_MCP_URL`, its ha-mcp webhook URL | `HA_MCP_URL_BUSH`, its webhook URL `http://10.0.0.254:8123/api/webhook/<id>` |
| Webhook id | The credential | A secret |
| Works from | Any network, through the remote address | Only that LAN (oryx is on it) |
| Token key | `HA_TOKEN` | `HA_TOKEN_BUSH`, the Wren user's long-lived token |

- `dojo.thecybermonk.com` is not Salt Lake's webhook host: that name is for Sami's primary instance, which moves to the SF Green.
- Salt Lake's webhook id at `http://10.0.51.23:8123/api/webhook/<id>` works only on that house's LAN.
- SF's address is static and set on the Green. It is outside the gateway's assumed DHCP pool. The gateway reserves nothing for it (`skills/majordomo/references/sites/wiz.md` in sjawhar/dojo).
- SF's webhook is in auth mode (below), so each call also sends `HA_TOKEN_BUSH`. The webhook answers 401 without it.
- There is no `HA_MCP_TOKEN_BUSH` secret, so the `bash -c` of each SF command maps the names.

| Run | Command |
| --- | --- |
| Salt Lake call | `secrets HA_MCP_URL -- ~/.dotfiles/plugins/sjawhar/skills/home-assistant/ha-mcp-call.sh <tool> '<json>'` |
| SF call | `secrets HA_MCP_URL_BUSH HA_TOKEN_BUSH -- bash -c 'export HA_MCP_URL="$HA_MCP_URL_BUSH" HA_MCP_TOKEN="$HA_TOKEN_BUSH"; exec ~/.dotfiles/plugins/sjawhar/skills/home-assistant/ha-mcp-call.sh "$@"' _ <tool> '<json>'` |
| SF MCP client | `secrets HA_MCP_URL_BUSH HA_TOKEN_BUSH -- bash -c 'export HA_MCP_TOKEN_BUSH="$HA_TOKEN_BUSH"; unset HA_TOKEN_BUSH; exec npx -y -p mcp-remote@0.14.3 mcp-remote-from-env HA_MCP_URL_BUSH --allow-http'` |

- Send the Green only a token you have seen work.
- Test the Green's refusals against a stub.
- Read `references/sf-green-lockout.md` when the Green answers 403 to every request from an address, or before you change its ban threshold.
- Do not start the stopped container `homeassistant-sf` on oryx: it is a rollback copy, not the SF instance.

## Credentials and auth mode

- Do not expand `$HA_MCP_URL` or a token into a command's arguments (`curl "$HA_MCP_URL"`, `mcp-remote "$HA_MCP_URL"`, `-H "Authorization: Bearer $TOKEN"`): every user can read `/proc/<pid>/cmdline`, and any agent's `ps` or `pgrep -af` prints the id into its transcript.
  - Pass them through the environment, a config file or stdin.
- The MCP launch in this skill's frontmatter runs `~/.dotfiles/scripts/mcp-remote-from-env`. It takes the URL from `HA_MCP_URL`, unsets it, runs `mcp-remote` 0.14.3 in its own process, and redacts the URL from stderr.
- **Auth mode:** a webhook whose HA-MCP Server option `webhook_auth` is `ha_auth` ("Sign in with Home Assistant") answers 401 without a long-lived HA token.
- Read `references/auth-mode.md` when a call answers 401, or before you call or launch a client for an auth-mode webhook other than with the SF commands above.
- ha-mcp runs inside HA: HACS component "HA-MCP Custom Component", entry "HA-MCP Server". The admin-only HA-MCP panel in the HA sidebar turns tools on and off and sets Read Only Mode, feature flags and edit backups.
- File and YAML tools (`ha_read_file`, `ha_config_set_yaml`, ...) are beta and off. They need the "HA-MCP File & YAML Tools" entry and its feature flags.

## Work

1. **Orient:** `ha_get_overview` (summary), `ha_search` (fuzzy entity and config lookup), `ha_get_state` (live states).
2. **Read and follow the bundled guide** before you write automations, scripts or helpers: `ha_get_skill_guide` with no arguments returns its `SKILL.md` and the list of its reference files. Pass `file` to read one.
3. **Read the BestPracticeKey again each session and each hour:** it rotates hourly. Config writes (`ha_config_set_automation/_script/_scene/_helper/_dashboard`) fail without the current key, which that no-argument answer holds.
   - Pass `MandatoryBPS=false` to skip receiving the reference content again.
4. **Validate Jinja2** with `ha_eval_template` before you put it in a config.
5. **Debug automations:** `ha_get_automation_traces`, `ha_get_history`, `ha_get_logs`.

## Safety

- Get the human's confirmation before deletes (`ha_config_remove_*`, `ha_remove_*`), `ha_restart`, and `ha_call_service` on locks, garage doors, climate entities and valves, even though the server backs up config edits.
- Call only the read tools (`readOnlyHint` in the tool annotations) when you audit.
- Do not print the raw `options` from `ha_get_app(slug=...)`: it returns all add-on `options`, secrets included (Advanced SSH `ssh.password`).
  - Select fields with `jq` (`.addon.options.ssh.authorized_keys`).
- `ha_manage_app` config mode merges one nested level: `{"ssh": {"authorized_keys": [...]}}` keeps the other `ssh.*` fields.

## Tool quirks

Read `references/tool-quirks.md` before you create or update an automation, install an update, or call `ha_set_entity`, `ha_remove_helpers_integrations`, `ha_manage_backup`, `ha_manage_hacs`, `ha_get_history`, `ha_manage_energy_prefs` or `ha_restart`. It gives the arguments each needs and what each answer means.

## Known behavior

| Behavior | Do |
| --- | --- |
| Matter commissioning (pairing) through MCP fails: the transport timeout aborts it | Pair in the HA web UI or companion app |
| Device-trigger automations (registry UUIDs) warn "not found in entity registry" on every write | Ignore it on a config that predates your edit |
| Renames do not reach energy prefs | Update `ha_manage_energy_prefs` explicitly |
| New entities on a device with an area get the area in their entity_id (`sensor.bedroom_2_office_equipment_…`), attached helpers too | Expect to rename |
| Card auto-discovery breaks unless a device's entities share one name prefix | Give a device's entities one name prefix (`kitchen_outlet_`) |
| Fans spoof mmWave occupancy | Require PIR motion as well as mmWave in an automation that acts on occupancy |

- Do not let an automation that turns a device on override a human's off.
  - Store a human action in a hold helper (`input_boolean.office_ac_hold`): context `parent_id` none marks it.
  - Check the hold helper first.
- Read `references/matter-and-tapo.md` when you work on Matter or Tapo devices or their firmware, or need a UI-only action that has no tool or service.
