---
name: apify
description: Use when scraping websites or listing sites, extracting structured data in bulk, running prebuilt scrapers (Apify Actors), or researching sites that block plain page fetches. Use it whenever apartment hunting, Airbnb or marketplace listings, anti-bot or JS-heavy sites, or batch data collection need a scraping platform, not browser clicks.
mcp:
  apify:
    command: secrets
    args: ["APIFY_TOKEN", "--", "npx", "-y", "@apify/actors-mcp-server"]
---

# Apify

Use an Apify Actor, not one-off browser scraping, when any one of these holds: the task needs many listings, the site is JS-heavy or blocks bots, a community Actor for the site exists, or the user wants structured output. On such sites browser scraping breaks often and returns unstructured text.

## Steps

1. Find an Actor for the site: `search-actors`.
2. Read the Actor's details and input schema: `fetch-actor-details`.
3. Run the Actor: `call-actor`.
4. Read the results: `get-dataset-items` with the run's `datasetId`.
5. Keep the fields the user asked for (for example price, location, URL), drop duplicate items, and give the user a table sorted by their main criterion.

Without an Actor:

- One page at a known URL: `apify--web-fetch`.
- A web search by query: `apify--rag-web-browser`.

## Calling the tools

- The `apify` MCP server supplies these tools; its config above gets `APIFY_TOKEN` from `secrets`.
- In omp, write JSON arguments to `xd://mcp__apify_<tool>`. To get `<tool>`, remove an `apify--` prefix and change dashes to underscores: `search-actors` is `xd://mcp__apify_search_actors`, and `apify--web-fetch` is `xd://mcp__apify_web_fetch`. Read that path first for the argument schema.
