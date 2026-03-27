---
name: apify
description: Use when scraping websites, extracting structured data from listing sites, running prebuilt scrapers/actors, or automating stubborn web research workflows. Make sure to use this whenever apartment hunting, marketplace scraping, anti-bot-resistant extraction, or batch website data collection would benefit from a dedicated scraping platform rather than ad hoc browser clicks.
mcp:
  apify:
    command: secrets
    args: ["APIFY_TOKEN", "--", "npx", "-y", "@apify/actors-mcp-server"]
    env:
      SOPS_AGE_KEY: "${SOPS_AGE_KEY}"
---

# Apify

Use Apify as the heavy-duty scraping and extraction layer.

It is especially useful when:
- a site is JS-heavy or anti-bot-prone
- we need many listings, not just one page
- a community Actor already exists for the target site
- we want structured output instead of manual copy/paste from the browser

## Best use cases

- Apartment / real-estate site scraping
- Airbnb / booking / marketplace extraction
- Bulk listing collection across multiple sites
- Converting messy listing pages into structured datasets

## MCP usage

Call via:

```python
skill_mcp(mcp_name="apify", tool_name="...", arguments={...})
```

## Recommended flow

1. Search for a relevant Actor.
2. Inspect the Actor details and inputs.
3. Run the Actor.
4. Read the dataset items.
5. Normalize results into a shortlist.

## Typical tools

### Search for a scraper

```python
skill_mcp(mcp_name="apify", tool_name="search-actors", arguments={"query": "airbnb scraper"})
```

### Use the built-in web extraction path

```python
skill_mcp(mcp_name="apify", tool_name="apify/rag-web-browser", arguments={"url": "https://example.com/listing"})
```

### Run a chosen Actor

```python
skill_mcp(mcp_name="apify", tool_name="call-actor", arguments={
  "actorId": "apify/example-actor",
  "input": {"startUrls": [{"url": "https://example.com"}]}
})
```

### Read results

```python
skill_mcp(mcp_name="apify", tool_name="get-dataset-items", arguments={"datasetId": "DATASET_ID"})
```

## Notes

- Requires `APIFY_TOKEN` in the environment.
- Apify is best when we need scale, repeatability, or structured output.
- Prefer Apify over fragile one-off browser scraping when the task looks repeatable.
