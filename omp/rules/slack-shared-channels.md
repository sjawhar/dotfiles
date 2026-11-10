---
description: Sending a Slack direct message to a person in Sami's name
condition:
  - '"channel_id"\s*:\s*"[UD][A-Z0-9]{6,}"'
scope: "tool:write(xd://mcp__slack_send_message), tool:write(xd://mcp__slack_send_message_draft), tool:write(xd://mcp__slack_schedule_message), tool:mcp__slack_send_message, tool:mcp__slack_send_message_draft, tool:mcp__slack_schedule_message"
interruptMode: tool-only
---
# Post in a shared channel, not a direct message

The Slack tools post as Sami, and a channel id that starts with `U` or `D` is a direct message to one person. Write to people in shared channels, so the whole team can see the request and its answer.

- Post in the project's channel, or the channel the topic already lives in, in a thread when one exists. Mention the person there.
- Send a direct message only when Sami has said yes to that message, because a direct message speaks for him privately and nobody else can see what was asked.
- To ask a person for a review or a reply on Dispatch, comment on the document or issue and mention them; that reaches them without Slack.
