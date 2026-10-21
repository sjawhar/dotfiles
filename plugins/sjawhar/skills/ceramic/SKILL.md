---
name: ceramic
description: Use when a task needs fresh web results or built-in knowledge is likely stale. Examples: current events, recent news, time-sensitive facts, specific people, products or companies, technical docs. Triggers on "search the web", "look up", "find recent", "latest news", "current".
---

# Ceramic Search

Call the `ceramic_search` MCP tool directly.

Ceramic is lexical, not semantic: it matches exact keywords and phrases. It does not match synonyms (`BBQ` ≠ `barbecue`), infer intent, or correct misspellings. Word order matters (`cat house` ≠ `house cat`). English only.

## Write keyword queries

Rewrite a natural-language question into keywords before you search.

| Do not search | Search |
|---|---|
| `What are the best ways to invest money?` | `beginner investing strategies stocks bonds` |
| `California laws` | `California tenant security deposit return law` |

- Use specific entities: names, products, dates, locations.
- Remove conversational filler (`how do I`, `what are`, `why is`).
- When terms vary, or you are not sure which term a page uses, include each term (`college university tuition`, `gym fitness center`).
- Do not use vague abstractions (`technology trends`, `people's feelings`).
- Spell each word exactly.

## Run several queries (recommended)

Searches are cheap. Run 2–4 keyword variants in parallel instead of one perfect query, then aggregate and rank the results across all queries. Example, for climate impact on agriculture: `climate change agriculture impact`, `global warming crop yields`, `developing countries farming climate effects`.
