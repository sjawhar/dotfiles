# Overseer API Reference

API patterns for sub-agents doing the overseer's information gathering and action work.

## Session Status
```bash
curl -s "http://127.0.0.1:$PORT/session/status?directory=$DIR"
```

## Read Recent Messages
```bash
curl -s "http://127.0.0.1:$PORT/session/$SID/message?directory=$DIR&limit=3" \
  | jq '[.[] | {role: .info.role, text: ([.parts[] | select(.type == "text") | .text] | join(" ") | .[:400])}]'
```

## Send Prompt (async)
```bash
curl -s -X POST "http://127.0.0.1:$PORT/session/$SID/prompt_async?directory=$DIR" \
  -H 'Content-Type: application/json' \
  -d '{"parts": [{"type": "text", "text": "Your message"}]}'
```

## Send with Model/Agent Override
```bash
curl -s -X POST "http://127.0.0.1:$PORT/session/$SID/prompt_async?directory=$DIR" \
  -H 'Content-Type: application/json' \
  -d '{"parts": [{"type": "text", "text": "..."}], "model": {"providerID": "openai", "modelID": "gpt-5.4"}, "agent": "hephaestus"}'
```

## Read Todos
```bash
curl -s "http://127.0.0.1:$PORT/session/$SID/todo?directory=$DIR" \
  | jq '[.[] | select(.status != "completed") | {content: (.content[:100]), status}]'
```

## Legion Workers
```bash
curl -s http://127.0.0.1:13370/workers | jq '[.[] | {id, status, sessionId, workspace}]'
```

For full API reference, load the `legion/opencode-api` skill.

## Visual Companion

The brainstorming skill's visual companion pattern — push HTML to a screen directory, user sees it in browser, capture clicks via `.events` file — works for the overseer too.

Uses:
- Status dashboards showing all workstreams at a glance
- Approval flows where the user clicks to approve/reject
- Priority selection interfaces
- Information-dense displays that don't work well in terminal
- Plans and status for review — offer to push to browser when presenting these

Pattern: push HTML → user sees and interacts → read `.events` → act on selections.
Tailscale hostname: `sami-agents-mx.tailb86685.ts.net` — accessible from user's phone.

Caveats:
- Terminal stays primary channel; visual is enhancement
- Don't depend on visual availability for core functionality

## Information Sources

| Source | Access | When |
|--------|--------|------|
| OpenCode sessions | Serve API (via sub-agent) | Always — primary view |
| GitHub | `gh pr list`, `gh pr view` (via sub-agent) | Verifying status, reviews |
| Legion daemon | `curl 127.0.0.1:13370/workers` (via sub-agent) | When controller is active |
| Google Drive | Load `google-workspace` skill (via sub-agent) | Standup notes, shared docs |
| Ghost Whisper | Load `ghost-wispr` skill (via sub-agent) | Live transcription, meeting context, priority discussions |
| Slack | Load `slack-bot` skill (via sub-agent) | Team communications, bug reports |
| Session history | `session_search`, `session_read` (via sub-agent) | Past context |
| Memory file | Read `~/.dotfiles/.overseer/memory.md` | Always — long-term knowledge |
| Priority docs | Read `~/.dotfiles/.overseer/priorities/` | Always — user-provided priorities |
