---
name: clarify
description: Tells the agent to expand only the parts of its last message the user could not decode, then continue its work.
disable-model-invocation: true
---

# Clarify

Parts of your last message are undecodable: a term you coined, a bare identifier, an acronym, or a reference I cannot resolve. There can be one or several.

- Name each one and expand it: what it refers to, where it came from, and why it is here. One or two sentences each is usually right.
- For a file, an artifact or infrastructure, say what it is for, not only where it is.
- Expand only the opaque parts, not the whole ask. If I had not found your question, I would have used `/re-ask`.
- Do not explain concepts I use myself, restate the parts I understood, or teach the topic. I am asking what something means.
- Then continue with your work.
