#!/bin/bash
# Direct MCP tool call to the ha-mcp server over its connect URL (webhook or loopback; stateless streamable HTTP).
# For batch/scripted operations the skill_mcp tool can't loop efficiently.
# Usage: secrets HA_MCP_URL -- ha-mcp-call.sh <tool_name> '<json_args>'
# A webhook in "Sign in with Home Assistant" mode also needs an HA access token: set HA_MCP_TOKEN
# and it goes out as `Authorization: Bearer`. Without HA_MCP_TOKEN no Authorization header is sent.
# The URL and the token are credentials, so curl reads them from a config file on a /dev/fd path
# (printf is a builtin) rather than from its arguments, which /proc/<pid>/cmdline shows to every user.
# --fail makes a refusal (401 without a valid token) print curl's HTTP error and exit non-zero.
set -euo pipefail
TOOL="$1"; ARGS="${2:-\{\}}"
curl_config() {
  printf 'url = "%s"\n' "$HA_MCP_URL"
  if [[ -n "${HA_MCP_TOKEN:-}" ]]; then
    printf 'header = "Authorization: Bearer %s"\n' "$HA_MCP_TOKEN"
  fi
}
curl -sS --fail --max-time 120 -X POST --config <(curl_config) \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
  -d "{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/call\",\"params\":{\"name\":\"$TOOL\",\"arguments\":$ARGS}}" \
  | grep '^data:' | sed 's/^data: //' | python3 -c "
import json,sys
for line in sys.stdin:
    d = json.loads(line)
    if d.get('id') == 2:
        for c in d.get('result',{}).get('content',[]):
            print(c.get('text',''))
"
