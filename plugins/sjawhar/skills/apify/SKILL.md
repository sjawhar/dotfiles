---
name: apify
description: "Use when scraping websites or listing sites, extracting structured data in bulk, running prebuilt scrapers (Apify Actors), or researching sites that block plain page fetches: apartment hunting, Airbnb or marketplace listings, anti-bot or JS-heavy sites, batch data collection."
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

With no Actor for the site, run a general one with `call-actor`: `apify/web-fetch` for one page at a known URL, `apify/rag-web-browser` for a web search by query.

## Calling the tools

In omp, write JSON arguments to `xd://mcp__apify_<tool>`, with the tool's dashes changed to underscores: `search-actors` is `xd://mcp__apify_search_actors`. Read that path first for the argument schema.
