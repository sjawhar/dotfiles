# Webhook in "Sign in with Home Assistant" mode

An ha-mcp webhook whose HA-MCP Server option `webhook_auth` is `ha_auth` ("Sign in with Home Assistant") answers 401 unless the call carries an HA access token as `Authorization: Bearer`. A long-lived access token works: the SF instance's is `HA_TOKEN_BUSH`, Salt Lake's is `HA_TOKEN`.

Both callers send the token when the variable named next to the URL holds it. They send no Authorization header when that variable is unset or empty. `secrets` exports each key under its own name and cannot rename it, so the `bash -c` of each command below maps the token key to the name the caller reads.

- `ha-mcp-call.sh` reads `HA_MCP_TOKEN` and puts it in curl's `--config`, like the URL. A refusal prints `curl: (22) The requested URL returned error: 401` and exits non-zero.
- `~/.dotfiles/scripts/mcp-remote-from-env <URL_NAME>` reads the variable named like `<URL_NAME>` with its `URL` part replaced by `TOKEN` (`HA_MCP_URL` → `HA_MCP_TOKEN`, `HA_MCP_URL_BUSH` → `HA_MCP_TOKEN_BUSH`). It unsets that variable, hands mcp-remote `--header "Authorization: Bearer <token>"` in its own process, and redacts the token from stderr.
- **Salt Lake call and launch in auth mode:** run the "SF call" and "SF MCP client" commands of SKILL.md with Salt Lake's names in place of SF's:

  | SF name | Salt Lake name |
  | --- | --- |
  | `HA_MCP_URL_BUSH` | `HA_MCP_URL` |
  | `HA_TOKEN_BUSH` | `HA_TOKEN` |
  | `HA_MCP_TOKEN_BUSH` | `HA_MCP_TOKEN` |
