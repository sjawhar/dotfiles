---
name: reading-spend
description: "Use when asked what a project, stream, repository, workflow, VM, model or person costs, or whether spend is worth it: cloud, CI, GitHub Actions, Azure, OpenAI, Anthropic dollars. Also before asking anyone for GitHub org or billing access to see costs: the data is already in BigQuery."
---

# Reading spend

All of Theorem's spend is in the BigQuery dataset `theorem-agent-infra.agent_spend`, which every agent identity can read: do not ask a person for GitHub organization, billing or console access to see costs. The full guide, with a query per question and the meaning of every column, is theorem-labs/agent-infra `spend/README.md`, "For agents: reading your project's spend".

## Run a query

```bash
bq query --project_id=theorem-agent-infra --use_legacy_sql=false --maximum_bytes_billed=5000000000 '<SQL>'
```

- Always pass `--maximum_bytes_billed`: nothing else stops a large query, and a full scan of `otel_raw` reads many GB.
- On a table you have not queried before, run with `--dry_run` first: it prints the bytes it would read and bills nothing.
- Refused `bigquery.jobs.create` in `theorem-agent-infra`: run the same query with `--project_id=autoformalization` or `--project_id=theorem-research`. If no project lets you run it, or the table itself says "Access Denied", report it on Dispatch (`dispatch search --query 'agent_spend access'`) instead of looking for another way in.

## Start from `project_spend_daily_v`

One row per UTC `day`, `project`, `platform`, `item` and `kind`, with `usd`.

- `project`: `BET`, `OSSV`, `LINUX`, `INFRA`, `LNB`, `Rocq speed`, or a repository's own name. Not a project: `(no project)` (bill remainders), `Agents, no stream`, `Untagged`, `People's machines` (colleagues' machines), `No owner found`.
- `platform` and `item`: `Anthropic`, `OpenAI`, `Other model providers` by model; `Google Cloud` by lane or machine group; `Azure` by service; `GitHub` by repository.
- The company's charged total: every row except kinds `not_billed` and `attributed_org_unknown`.
- Ours: also leave out kinds `colleague`, `caller_unknown`, `other_project`, `overhead`, `credit`, and projects `People's machines` and `No owner found`.
- Today and yesterday still grow: OpenAI posts a day's costs during that day and the next.

A project's week by platform:

```sql
SELECT platform, ROUND(SUM(usd), 2) AS usd
FROM `theorem-agent-infra.agent_spend.project_spend_daily_v`
WHERE project = 'BET' AND day >= DATE_SUB(CURRENT_DATE(), INTERVAL 6 DAY)
  AND kind NOT IN ('attributed_org_unknown', 'not_billed', 'colleague', 'caller_unknown', 'other_project', 'overhead', 'credit')
GROUP BY platform ORDER BY usd DESC
```

To drill down, group by `item` and filter `platform`: `'GitHub'` gives repositories, `IN ('Google Cloud', 'Azure')` gives lanes and machine groups, a model platform gives models.

## Workflow and job cost: `project_actions_daily_v`

GitHub Actions by `project`, `repository`, `workflow`, `runner` and `outcome`, with `minutes` and `usd` at list price. It is the same money as the `GitHub` rows above counted another way: never add the two views. `outcome` is NULL on rows for 2026-10-28 and 10-29, which hold every outcome of those days together.

```sql
SELECT repository, workflow, runner, outcome, SUM(minutes) AS minutes, ROUND(SUM(usd), 2) AS usd
FROM `theorem-agent-infra.agent_spend.project_actions_daily_v`
WHERE project = 'BET' AND day >= DATE_SUB(CURRENT_DATE(), INTERVAL 6 DAY)
GROUP BY 1, 2, 3, 4 ORDER BY usd DESC LIMIT 20
```

## Other questions

- One person's spend, and each source's freshness: the README section has the queries. When a number looks too low, check freshness first: a source whose last `day` is before yesterday stopped loading.
- Never name, stop, resize or act on a machine that is not ours. The views show colleagues only as `People's machines` and `colleague`; the tables `cloud_cost_estimated`, `cloud_cost_today` and `billed_usage_caller` name them, so do not read names from those tables.
