---
name: reading-spend
description: "Use when asked what a project, stream, repository, workflow, VM, model or person costs, or whether spend is worth it: cloud, CI, GitHub Actions, Azure, OpenAI, Anthropic dollars. Also before asking anyone for GitHub org or billing access to see costs: the data is already in BigQuery."
---

# Reading spend

All of Theorem's spend is in BigQuery dataset `theorem-agent-infra.agent_spend`. Every identity an agent runs as can read it, so never ask a person for GitHub organization, billing or console access to see costs. The full guide, with one query per question, is theorem-labs/agent-infra `spend/README.md`, section "For agents: reading your project's spend".

## Run a query

```bash
bq query --project_id=theorem-agent-infra --use_legacy_sql=false --maximum_bytes_billed=5000000000 '<SQL>'
```

- Always pass `--maximum_bytes_billed=5000000000` (5 GB). Nothing else stops a large query: no quota caps the project, and a `SELECT *` on `otel_raw` reads about 19 GB. The queries here bill under 600 MB.
- Run the same command with `--dry_run` first on a table you have not queried before: it prints the bytes it would read and bills nothing.
- If the job is refused for `bigquery.jobs.create` in `theorem-agent-infra`, run it with `--project_id=autoformalization` or `--project_id=theorem-research` (a colleague's box identity and the OSS Verify lane VMs hold the right there). The Verified Linux agent hosts and Legion's pods can open the tables but cannot run a query yet: say so to the cost tracking lead (dispatch://INFRA-165) instead of looking for another way. An "Access Denied" on the table itself means your identity is not a reader: tell the same lead.

## Start from `project_spend_daily_v`

One row per UTC `day`, `project`, `platform`, `item`, `kind`, with `usd`. Since agent-infra#2436 (2026-11-08) every charged dollar is on a project or on a named row that is not ours.

- `project`: `BET`, `OSSV`, `LINUX`, `INFRA`, `LNB`, `Rocq speed`; catch-all labels (`Agents, no stream`, `untagged`) are not a project. `(no project)` holds bill remainders a record cannot place yet. `People's machines` is colleagues' own machines (Google Cloud and Azure); `No owner found` is cloud spend nobody claims.
- `platform` and `item`: `Anthropic`, `OpenAI`, `Other model providers` by model; `Google Cloud` by lane or machine group; `Azure` by service; `GitHub` by repository.
- `kind`: `attributed` (our sessions' calls), `estimated` (Google Cloud and Azure at list), `billed` (bill rows placed on a project), the signed remainders `billed_unattributed`, `cost_report_unattributed`, `estimated_unattributed`, and five kinds that are **not ours**: `colleague` (colleagues' share of an Anthropic PBC bill, one row per day and organization), `caller_unknown` (bill dollars no report can assign), `other_project` (OpenAI use by keys not ours), `overhead` (Google Support, GitHub seats), `credit` (Google's promotional credit, negative). `not_billed` is HDO, which costs nothing; `attributed_org_unknown` may be in a bill already. A column `provisional` marks an Anthropic split until both of its reports were read 24 hours after the day.
- **The company's charged total** = every row except `not_billed` and `attributed_org_unknown`. **Ours** = also leave out `colleague`, `caller_unknown`, `other_project`, `overhead`, `credit` and projects `People's machines` and `No owner found`.
- `Google Cloud` is counted before Google's promotional credit (from 2026-11-06 the credit pays most of the bill); the `credit` row brings the platform back to the bill Google charges. The bill less Support less the list estimate is split, signed, between ours, colleagues and nobody claims.
- OpenAI's report posts a day's costs during that day and the next, so today and yesterday still grow by the hour.

My project this week by platform:

```sql
SELECT platform, ROUND(SUM(usd), 2) AS usd
FROM `theorem-agent-infra.agent_spend.project_spend_daily_v`
WHERE project = 'BET' AND day >= DATE_SUB(CURRENT_DATE(), INTERVAL 6 DAY) AND kind NOT IN ('attributed_org_unknown', 'not_billed', 'colleague', 'caller_unknown', 'other_project', 'overhead', 'credit')
GROUP BY platform ORDER BY usd DESC
```

Change `platform` in `GROUP BY` to `item` and filter `platform` to drill down: `platform = 'GitHub'` gives repositories, `platform IN ('Google Cloud', 'Azure')` gives lanes and machine groups, and the model platforms give models.

## Workflow and job cost: `project_actions_daily_v`

GitHub Actions by `project`, `repository`, `workflow`, `runner`, `outcome`, with `minutes` and `usd` at list price. It is the same money as the `GitHub` rows above counted another way, so never add the two views together. It ends on 2026-10-31 until its loader is back, and `outcome` is NULL on rows loaded before that column existed (10-28 and 10-29), which hold every outcome of those days together.

```sql
SELECT repository, workflow, runner, outcome, SUM(minutes) AS minutes, ROUND(SUM(usd), 2) AS usd
FROM `theorem-agent-infra.agent_spend.project_actions_daily_v`
WHERE project = 'BET' AND day >= DATE_SUB(CURRENT_DATE(), INTERVAL 6 DAY)
GROUP BY 1, 2, 3, 4 ORDER BY usd DESC LIMIT 20
```

## Other questions

- One person's spend, how fresh each source is: the README section has the exact queries. Check freshness first when a number looks too low: a source whose last `day` is older than yesterday stopped loading.
- Never name, stop, resize or act on a machine that is not ours. The views show colleagues' machines only as `People's machines` and colleagues' model calls only as `colleague` (one row per organization, never per person), but the tables (`cloud_cost_estimated`, `cloud_cost_today`, `billed_usage_caller`) name them: do not read names from those tables, and treat colleagues' rows as out of scope.
