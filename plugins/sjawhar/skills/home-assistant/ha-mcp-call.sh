#!/bin/bash
# Direct MCP tool call to the ha-mcp server over its webhook URL (stateless streamable HTTP).
# For batch/scripted operations the skill_mcp tool can't loop efficiently.
# Usage: secrets HA_MCP_URL -- ha-mcp-call.sh <tool_name> '<json_args>'
set -euo pipefail
TOOL="$1"; ARGS="${2:-\{\}}"
curl -sS --max-time 120 -X POST "$HA_MCP_URL" \
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
