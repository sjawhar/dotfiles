---
name: apify
description: "Use when scraping websites or listing sites, extracting structured data in bulk, running prebuilt scrapers (Apify Actors), or researching fetch-blocking sites, and for apartment, Airbnb or marketplace listings, anti-bot or JS-heavy sites or batch collection that need a scraper, not browser clicks."
mcp:
  apify:
    command: secrets
    args: ["APIFY_TOKEN", "--", "npx", "-y", "@apify/actors-mcp-server"]
---

# Apify

Use an Apify Actor, not one-off browser scraping, when any one of these holds: the task needs more listings than one page of results shows, the site is JS-heavy or blocks bots, a community Actor for the site exists, or the user wants structured output. On such sites browser scraping breaks often and returns unstructured text.

## Steps

1. Find an Actor for the site: `search-actors`.
2. Read the Actor's details and input schema: `fetch-actor-details`.
3. Run the Actor: `call-actor`.
4. Read the results: `get-dataset-items` with the run's `datasetId`.
5. Keep the fields the user asked for (for example price, location, URL), drop duplicate items, and give the user a table. Sort it by the first criterion the user named; when they named none, sort by price if the items have one, else keep the Actor's order. Say which order you used.

With no Actor for the site, call a general tool directly: `apify--web-fetch` for one page at a known URL, `apify--web-search` for a web search by query.

## Calling the tools

The `apify` MCP server supplies these tools; its config above gets `APIFY_TOKEN` from `secrets`.

In omp (oh-my-pi, the agent harness; its sessions have `xd://` tool devices), write JSON arguments to `xd://mcp__apify_<tool>`. To get `<tool>`, remove an `apify--` prefix and change dashes to underscores: `search-actors` is `xd://mcp__apify_search_actors`, and `apify--web-fetch` is `xd://mcp__apify_web_fetch`. Read that path first for the argument schema.
