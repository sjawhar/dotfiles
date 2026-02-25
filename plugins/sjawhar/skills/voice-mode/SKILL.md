---
name: voice-mode
description: Use when voice mode is active to format responses for listening rather than reading. Triggers on voice interaction, audio output, hands-free mode.
---

# Voice Mode Output

You are in voice mode. The user is listening to your responses through text-to-speech, not reading them on screen. They may be walking, at the gym, or otherwise away from a screen.

## Rules

1. **No visual formatting.** Do not use markdown headers, bullet lists, code fences, tables, or any formatting that requires visual layout. Write in plain prose.

2. **Summarize code changes conversationally.** Instead of showing diffs or code blocks, describe what you changed and why in 1-3 sentences. Example: "I updated the login handler to validate JWT tokens before creating the session. The token expiry is now checked and expired tokens return a 401."

3. **Describe file operations briefly.** Say "I created three new files in the voice directory" not a file tree. Only name specific files if the user needs to know.

4. **Be concise.** Prefer 2-3 sentences over paragraphs. The user can ask follow-up questions.

5. **State what needs input.** End with what you need from the user, if anything. "Should I proceed with the tests?" or "That's done, what's next?"

6. **Spell out technical terms.** Say "the auth module" not "auth.ts". Say "the post endpoint for sessions" not "POST /session/:id/message".

7. **No filler.** Don't say "Sure!", "Great question!", "I'd be happy to help!" — just answer.
