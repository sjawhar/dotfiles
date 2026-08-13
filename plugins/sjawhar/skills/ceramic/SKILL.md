---
name: ceramic
description: Search the web via Ceramic Search (lexical/keyword-based). Use when looking up current events, recent news, time-sensitive facts, specific people/products/companies, technical docs, or any topic requiring fresh web results. Triggers on "search the web", "look up", "find recent", "latest news", "current", or when built-in knowledge is likely stale.
---

# Ceramic Search

Lexical (keyword-based) web search. Configured as a global MCP in `~/.config/opencode/opencode.json` — invoke the `ceramic_search` tool directly (not via `skill_mcp`).

## How It Works

Ceramic is **lexical**, not semantic. It matches **exact keywords and phrases** — no synonym expansion, no intent inference. Word order matters (`cat house` ≠ `house cat`).

## Querying — Keywords, Not Sentences

**Rewrite natural-language questions into keyword queries before searching.**

| ❌ Don't                                            | ✅ Do                                            |
| -------------------------------------------------- | ----------------------------------------------- |
| `What are the best ways to invest money?`         | `beginner investing strategies stocks bonds`    |
| `California laws`                                  | `California tenant security deposit return law` |

**Rules:**
- Use specific entities (names, products, dates, locations)
- Drop conversational filler (`how do I`, `what are`, `why is`)
- Include explicit synonyms when terminology varies (`college university tuition`)
- Avoid vague abstractions (`technology trends`, `people's feelings`)

## Multi-Query Strategy (Recommended)

Search is cheap. **Fire 2–4 keyword variants in parallel** instead of crafting one perfect query.

Example — user asks about climate impact on agriculture:
```
ceramic_search("climate change agriculture impact")
ceramic_search("global warming crop yields")
ceramic_search("developing countries farming climate effects")
```

Aggregate and rank results across queries.

## Limitations

- **Synonyms not matched**: `BBQ` ≠ `barbecue`, `gym` ≠ `fitness center` — include both if unsure
- **Misspellings fail**: exact spelling required
- **Conversational queries fail**: rewrite into keywords first
- **English only** (other languages coming)
