Route immediately — no permission needed. This is Tier 1 (autonomous: route bug reports with full context).

I'd dispatch a sub-agent to send a prompt to the web UI worker in `OpenCode/default` with both bugs, full context, and the user's exact words:

---

**Message to web worker:**

Two TTS bugs from the user:

1. **Backtick content read as "code"**: The voice-to-speech engine reads everything inside backticks as the literal word "code" instead of the actual content. Example: `` `dog` `` is spoken as "code" instead of "dog". The TTS rendering likely strips or replaces inline code markup with a placeholder label instead of extracting the text content.

2. **Sub-agent responses read aloud**: TTS is reading sub-agent responses in addition to what appears in the main UI thread. It should only read content visible to the user in the primary conversation, not background agent output.

---

**Why no permission check:**
- The overseer skill is explicit: "When the user reports a bug or requests a feature, dispatch a sub-agent to send it immediately. Don't ask for confirmation."
- Routing information to workers is Tier 1 — autonomous, report after.
- The user already told me who to send it to ("tell the web worker") and gave complete reproduction details. There's nothing ambiguous to clarify.
- Asking "should I send this?" would waste the user's time re-confirming something they literally just asked me to do.

**After sending**, I'd tell the user: "Sent both TTS bugs to the web UI worker — the backtick-as-'code' issue and the sub-agent bleed-through."
