---
name: apify
description: "Use when scraping websites or listing sites, extracting structured data in bulk, running prebuilt scrapers (Apify Actors), or researching fetch-blocking sites, and for apartment, Airbnb or marketplace listings, anti-bot or JS-heavy sites or batch collection that need a scraper, not browser clicks."
mcp:
  apify:
    command: secrets
    args: ["APIFY_TOKEN", "--", "npx", "-y", "@apify/actors-mcp-server"]
---

# Apify

Use an Apify Actor, not one-off browser scraping, when any one of these holds:

- The task needs more listings than one page of results shows.
- The site is JS-heavy: a plain fetch of the page returns no listings, because JavaScript builds them in the browser.
- The site blocks bots.
- A community Actor for the site exists.
- The user wants structured output.

**Why:** On such sites browser scraping breaks often and returns unstructured text.

## Steps

1. Find an Actor for the site: `search-actors`.
2. Read the Actor's details and input schema: `fetch-actor-details`.
3. Run the Actor: `call-actor`.
4. Read the results: `get-dataset-items` with the run's `datasetId`.
5. Keep the fields the user asked for (for example price, location, URL).
6. Drop duplicate items:

   | Items | A duplicate is |
   | --- | --- |
   | With a URL | An item with the same URL as an earlier item |
   | Without a URL | An item with the same values in every kept field |

7. Give the user a table.
8. Sort the table:

   | Case | Do |
   | --- | --- |
   | The user named a criterion | Sort by the first criterion the user named |
   | The user named none, and the items have a price | Sort by price, lowest first |
   | The user named none, and the items have no price | Keep the Actor's order |

9. Say which order you used.

Call a general tool directly when there is no Actor for the site:

| Need | Tool |
| --- | --- |
| One page at a known URL | `apify--web-fetch` |
| A web search by query | `apify--web-search` |

## Calling the tools

The `apify` MCP server supplies these tools. Its config above gets `APIFY_TOKEN` from `secrets`.

Write JSON arguments to `xd://mcp__apify_<tool>` in omp (oh-my-pi, the agent harness, whose sessions have `xd://` tool devices). Read that path first for the argument schema.

Remove an `apify--` prefix and change dashes to underscores to get `<tool>`:

| Tool | Path |
| --- | --- |
| `search-actors` | `xd://mcp__apify_search_actors` |
| `apify--web-fetch` | `xd://mcp__apify_web_fetch` |
