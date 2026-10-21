---
name: apify
description: Use when scraping websites or listing sites, extracting structured data in bulk, running prebuilt scrapers (Apify Actors), or automating stubborn web research. Use it whenever apartment hunting, Airbnb or marketplace listings, anti-bot or JS-heavy sites, or batch data collection need a scraping platform, not browser clicks.
mcp:
  apify:
    command: secrets
    args: ["APIFY_TOKEN", "--", "npx", "-y", "@apify/actors-mcp-server"]
---

# Apify

Use Apify as the heavy scraping and extraction layer. It is best for scale, repeatability or structured output. Prefer it to fragile one-off browser scraping when the task looks repeatable.

It is especially useful when:

- You need many listings, not one page.
- The site is JS-heavy or anti-bot-prone.
- A community Actor exists for the target site.
- You want structured output, not copy/paste from a browser.

## Steps

1. Find an Actor for the site: `search-actors`.
2. Read the Actor's details and input schema: `fetch-actor-details`.
3. Run the Actor: `call-actor`.
4. Read the results: `get-dataset-items` with the run's `datasetId`.
5. Normalize the results into a shortlist.

Without an Actor:

- One page at a known URL: `apify--web-fetch`.
- A web search by query: `apify--rag-web-browser`.

## Calling the tools

- The `apify` MCP server supplies these tools. It needs `APIFY_TOKEN`, which the MCP config gets from `secrets`.
- In omp, write JSON arguments to `xd://mcp__apify_<tool>`. To get `<tool>`, remove an `apify--` prefix and change dashes to underscores: `search-actors` is `xd://mcp__apify_search_actors`, and `apify--web-fetch` is `xd://mcp__apify_web_fetch`. Read that path first for the argument schema.
