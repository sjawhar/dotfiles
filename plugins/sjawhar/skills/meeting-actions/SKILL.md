---
name: meeting-actions
description: Process ghost-wispr summary-ready Envoy events into GitHub issues and Slack summaries. Use when setting up or running a persistent OpenCode session subscribed to notifications.ghost-wispr.summary-ready.
disable-model-invocation: true
---

# Meeting Actions

Turn completed Ghost Wispr meetings into tracked follow-through.

This is a **specific** workflow for `ghost-wispr summary-ready event -> transcript fetch -> LLM extraction -> GitHub issues -> Slack summary`.

## Quick Start

1. Configure the target repo, Slack channel, model, and tracking file.
2. Start a dedicated OpenCode session and load this skill.
3. Subscribe to Envoy:

   ```
   envoy_subscribe(topics=["notifications.ghost-wispr.summary-ready"])
   ```

4. Keep that session running.
5. When an event arrives, follow the workflow below without adding a human approval step.

## Required Tools

- `envoy_subscribe`, `envoy_list`, `envoy_unsubscribe` for the event stream
- Ghost Wispr REST API via `curl`
- `gh issue create` for GitHub issue creation
- `skill_mcp(... slack ...)` via the `slack-bot` skill for Slack posting
- `Read` / `Write` / `Edit`-style file access for the idempotency file

Related references:

- `ghost-wispr` skill for API usage patterns
- `slack-bot` skill for Slack posting patterns

## Configuration

Set or decide these values before processing live events:

- `GHOST_WISPR_HOST` — defaults to `http://localhost:8080` if unset
- `MEETING_ACTIONS_REPO` — target repo, for example `sjawhar/ghost-wispr`
- `MEETING_ACTIONS_SLACK_CHANNEL` — Slack destination, for example `#engineering`
- `MEETING_ACTIONS_MODEL` — `gemini` or `claude`
- `MEETING_ACTIONS_TRACKING_FILE` — default: `~/.config/opencode/meeting-actions-processed.json`
- `MEETING_ACTIONS_LABEL` — default: `meeting-action`

If repo or Slack channel is unknown, stop and configure them before creating issues.

## Envoy Trigger and Event Format

Subscribe to:

```
notifications.ghost-wispr.summary-ready
```

Ghost Wispr publishes an Envoy envelope with metadata in `payload_summary`.

Example envelope shape:

```json
{
  "event_id": "evt_123",
  "source": "ghost-wispr",
  "topic": "notifications.ghost-wispr.summary-ready",
  "dedupe_key": "session_abc",
  "payload_summary": "{\"session_id\":\"session_abc\",\"title\":\"Weekly product sync\",\"summary_preset\":\"meeting\",\"started_at\":\"2026-04-02T14:00:00Z\",\"ended_at\":\"2026-04-02T14:37:00Z\",\"duration_seconds\":2220}"
}
```

Decode `payload_summary` before doing anything else.

## Idempotency

Track processed meetings in:

```
~/.config/opencode/meeting-actions-processed.json
```

Recommended file shape:

```json
{
  "processed": {
    "session_abc": {
      "status": "completed",
      "processed_at": "2026-04-02T15:10:00Z",
      "issue_urls": [
        "https://github.com/sjawhar/ghost-wispr/issues/123"
      ],
      "slack_channel": "#engineering"
    }
  }
}
```

Use these states:

- `processing` — written immediately after dedupe passes
- `completed` — all intended issues created and Slack summary posted
- `completed_with_errors` — issue creation partially failed after one retry; Slack summary was still posted with failures
- `llm_failed` — extraction was invalid or unusable; no issues created

Rules:

1. If `session_id` already exists with `completed` or `completed_with_errors`, skip the event.
2. Write `processing` before fetching the transcript.
3. On LLM failure, update the entry to `llm_failed`, log it, and stop.
4. After GitHub issue creation finishes, preserve created issue URLs in the file so retries do not create duplicates.

## Workflow

### 1. Receive the Envoy event

- Confirm `source == "ghost-wispr"`
- Confirm `topic == "notifications.ghost-wispr.summary-ready"`
- Parse `payload_summary` into:
  - `session_id`
  - `title`
  - `summary_preset`
  - `started_at`
  - `ended_at`
  - `duration_seconds`

If `session_id` is missing, log and stop.

### 2. Run the idempotency check

- Read `MEETING_ACTIONS_TRACKING_FILE`
- Create it if missing with `{"processed":{}}`
- If the `session_id` is already marked `completed` or `completed_with_errors`, skip the event
- Otherwise write a `processing` entry immediately

### 3. Fetch the full meeting record

Use Ghost Wispr's session detail endpoint:

```bash
curl -s "$GHOST_WISPR_HOST/api/sessions/$SESSION_ID"
```

Expect:

```json
{
  "session": {
    "id": "session_abc",
    "title": "Weekly product sync",
    "summary": "...",
    "canonical_transcript": "..."
  },
  "segments": [
    {
      "speaker": "Ben",
      "text": "I'll handle the rollout checklist"
    }
  ]
}
```

Build the LLM input like this:

1. Prefer a speaker-labeled transcript assembled from `segments`
2. Include timestamps when available
3. Fall back to `session.canonical_transcript`
4. If that is empty, fall back to `session.refined_transcript`
5. Include `session.summary` and the metadata from `payload_summary`

### 4. Extract actions with the LLM

Use the configured model, but require a strict JSON response.

Prompt template:

```
You are extracting concrete outcomes from a meeting transcript.

Return ONLY valid JSON with this exact shape:
{
  "action_items": [
    {
      "title": "string",
      "description": "string",
      "assignee": "string",
      "priority": "high|medium|low",
      "quotes": ["string"]
    }
  ],
  "decisions": [
    {
      "title": "string",
      "context": "string",
      "quotes": ["string"]
    }
  ],
  "follow_ups": [
    {
      "title": "string",
      "context": "string"
    }
  ]
}

Rules:
- Extract only commitments, decisions, and unresolved follow-ups that are supported by the transcript or summary.
- Do not invent owners, dates, priorities, or work items.
- Use "unassigned" when no assignee is explicit.
- Keep action titles short and issue-ready.
- Put enough context in each description for a GitHub issue body.
- Quotes must be short verbatim supporting snippets from the transcript.
- If there are no action items, return an empty array.
- If the meeting is too ambiguous to extract safely, return empty arrays rather than guessing.

Meeting metadata:
<insert session metadata JSON>

Meeting summary:
<insert session.summary>

Transcript:
<insert speaker-labeled transcript>
```

Validation rules:

- The response must parse as JSON
- Top-level keys must be `action_items`, `decisions`, and `follow_ups`
- Each action item must have `title`, `description`, `assignee`, `priority`, and `quotes`
- If parsing or validation fails, treat it as an LLM failure: log it, set `llm_failed`, and create no issues

### 5. Create GitHub issues

Create one issue per extracted action item.

Command pattern:

```bash
gh issue create \
  --repo "$MEETING_ACTIONS_REPO" \
  --title "$ACTION_TITLE" \
  --label "$MEETING_ACTIONS_LABEL" \
  --body "$ISSUE_BODY"
```

Recommended issue body template:

```markdown
## Meeting
- Title: <meeting title>
- Session ID: <session id>
- Started: <started_at>
- Ended: <ended_at>
- Duration: <duration_seconds> seconds

## Action
- Assignee: <assignee>
- Priority: <priority>

## Context
<description>

## Supporting quotes
- "<quote 1>"
- "<quote 2>"

## Related decisions
- <decision title>

## Follow-ups
- <follow-up title>
```

Error handling:

1. If `gh issue create` fails, retry once.
2. If it still fails, record that action item as failed.
3. Continue processing the remaining action items.
4. Report failures in the Slack summary.

Never create placeholder or garbage issues when extraction is uncertain.

### 6. Post the Slack summary

Prefer the `slack-bot` skill's MCP server.

Pattern:

```python
skill_mcp(
  mcp_name="slack",
  tool_name="conversations_add_message",
  arguments={
    "channel_id": "#engineering",
    "payload": "*Meeting:* Weekly product sync\n*Duration:* 37m\n*Issues:* <https://github.com/sjawhar/ghost-wispr/issues/123|#123> rollout checklist\n*Decisions:* Use Envoy for summary delivery",
    "content_type": "text/plain"
  }
)
```

Slack summary format:

- Meeting title
- Meeting date / duration
- Action items created, with issue links
- Any action items that failed issue creation after retry
- Key decisions
- Follow-ups worth revisiting

Use Slack mrkdwn, not markdown headings.

Direct Slack Web API fallback if MCP is unavailable:

```bash
curl -s -X POST "https://slack.com/api/chat.postMessage" \
  -H "Authorization: Bearer $SLACK_BOT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"channel":"'$MEETING_ACTIONS_SLACK_CHANNEL'","text":"Meeting actions posted"}'
```

### 7. Finalize tracking state

- If all issues were created and Slack succeeded, mark the session `completed`
- If some issue creations failed after retry but Slack succeeded, mark `completed_with_errors`
- Preserve created issue URLs in the tracking file
- Store `processed_at` in RFC3339 format

## Error Handling Summary

- **LLM failure**: log the failure, mark `llm_failed`, stop, create no issues
- **GitHub failure**: retry once, then include the failure in Slack and mark `completed_with_errors`
- **Slack failure**: log it and keep the saved GitHub issue URLs in the tracking file so the session is not reprocessed blindly
- **Duplicate event**: skip by `session_id`

## Persistent Subscriber Setup

### Recommended: always-running OpenCode session

1. Start a dedicated OpenCode session for meeting actions.
2. Load this skill.
3. Confirm configuration values are available.
4. Subscribe with:

   ```
   envoy_subscribe(topics=["notifications.ghost-wispr.summary-ready"])
   ```

5. Verify the active subscription with `envoy_list()`.
6. Leave the session running so Envoy can deliver events as they arrive.

### Durable delivery option

If Envoy/NATS JetStream durability is configured for this topic, queued messages can survive subscriber downtime and be consumed later.

Use this when the session cannot stay online continuously, but still keep the local idempotency file because durable consumers can redeliver messages.

## Operating Notes

- This workflow is intentionally direct: no approval queue, no generic workflow engine, no action retraction logic.
- Do not push meeting logic back into Ghost Wispr.
- Do not reference any OpenClaw-based flow.
- Favor precise extraction over volume. Zero issues is better than bad issues.
