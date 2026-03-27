---
name: smrey-groceries
description: Use when buying groceries, shopping on smrey.com, or placing delivery orders from Supermercados Rey.
---

# Grocery Shopping (smrey.com)

Fetch and follow the headless grocery API skill:
https://gist.github.com/sjawhar/996255e13f2fcb080e53549bd5b44b60

1. Fetch the gist above and read `SKILL.md` — it has the complete API reference.
2. Read `AGENTS.md` in the workspace root for shopping preferences and product lookup table.
3. Credentials are in `.env` (`SMREY_USERNAME`, `SMREY_PASSWORD`) — never display them.
4. All API calls use `curl` — no browser automation.
