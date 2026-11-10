#!/bin/bash
# Direct MCP tool call to ha-mcp over its connect URL (webhook or loopback).
# For batch/scripted operations the skill_mcp tool can't loop efficiently.
# Usage: secrets HA_MCP_URL -- ha-mcp-call.sh <tool_name> '<json_args>'
# A webhook in "Sign in with Home Assistant" mode also needs an HA access token: set HA_MCP_TOKEN
# and it goes out as `Authorization: Bearer`. Without HA_MCP_TOKEN no Authorization header is sent.
# The URL and the token are credentials, so curl reads them from a config file on a /dev/fd path
# (printf is a builtin) rather than from its arguments, which /proc/<pid>/cmdline shows to every user.
# --fail makes a refusal (401 without a valid token) print curl's HTTP error and exit non-zero.
# The script prints each text content and exits 0; when curl fails (such as a 401 refusal), the
# answer has a JSON-RPC error or an isError result, or no answer arrives, it exits non-zero with
# the error on stderr.
set -euo pipefail
TOOL="$1"; ARGS="${2:-\{\}}"
curl_config() {
  printf 'url = "%s"\n' "$HA_MCP_URL"
  if [[ -n "${HA_MCP_TOKEN:-}" ]]; then
    printf 'header = "Authorization: Bearer %s"\n' "$HA_MCP_TOKEN"
  fi
}
answer=$(curl -sS --fail --max-time 120 -X POST --config <(curl_config) \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
  -d "{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/call\",\"params\":{\"name\":\"$TOOL\",\"arguments\":$ARGS}}")
printf '%s' "$answer" | python3 -c '
import json
import re
import sys


tool = sys.argv[1]
body = sys.stdin.read()


def no_answer(reason):
    print(f"ha-mcp-call.sh: no answer to {tool}: {reason}", file=sys.stderr)
    raise SystemExit(1)


def event_stream_data(stream):
    """The data of each event: its data: values joined by newlines (events end at a blank line)."""
    events = []
    data = []
    for line in re.split(r"\r\n|\r|\n", stream):
        if line == "":
            if data:
                events.append("\n".join(data))
            data = []
        elif line.startswith("data:"):
            value = line[5:]
            data.append(value[1:] if value.startswith(" ") else value)
    if data:
        events.append("\n".join(data))
    return events


events = event_stream_data(body)
if events:
    documents, not_json = events, "the data of an event is not valid JSON (cut off?)"
elif body.strip():
    documents, not_json = [body], "the response has no data: line and is not valid JSON"
else:
    no_answer("the response is empty")
messages = []
for document in documents:
    if not document.strip():
        continue  # an event with empty data, such as the MCP priming event, carries no message
    try:
        parsed = json.loads(document)
    except json.JSONDecodeError:
        no_answer(not_json)
    messages.extend(parsed if isinstance(parsed, list) else [parsed])

for message in messages:
    if not isinstance(message, dict) or message.get("id") != 2:
        continue
    if "error" in message:
        error = message["error"]
        if isinstance(error, dict):
            detail, code = error.get("message"), error.get("code")
            if code is not None:
                detail = f"{detail} (code {code})"
        else:
            detail = error
        print(f"ha-mcp-call.sh: {tool} failed: {detail}", file=sys.stderr)
        raise SystemExit(1)
    if "result" not in message or not isinstance(message["result"], dict):
        print(
            f"ha-mcp-call.sh: {tool} failed: the answer has neither result nor error",
            file=sys.stderr,
        )
        raise SystemExit(1)
    result = message["result"]
    content = result.get("content")
    content = content if isinstance(content, list) else []
    failed = result.get("isError") is True
    if failed:
        print(f"ha-mcp-call.sh: {tool} failed:", file=sys.stderr)
    for item in content:
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            print(item["text"], file=sys.stderr if failed else sys.stdout)
    raise SystemExit(1 if failed else 0)

no_answer("no message with id 2")
' "$TOOL"
