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

- Always pass `--maximum_bytes_billed=5000000000` (5 GB): no quota caps the project, and the queries here bill under 600 MB.
- If the job is refused for `bigquery.jobs.create` in `theorem-agent-infra`, run it with `--project_id=autoformalization` or `--project_id=theorem-research` (a colleague's box identity and the OSS Verify lane VMs hold the right there). An "Access Denied" on the table itself means your identity is not a reader: tell the cost tracking lead (dispatch://INFRA-165).

## Start from `project_spend_daily_v`

One row per UTC `day`, `project`, `platform`, `item`, `kind`, with `usd`.

- `project`: `BET`, `OSSV`, `LINUX`, `INFRA`; other streams by their own name (`Rocq speed`, `lean-speed`, `shared`, `untagged`). `(no project)` is the part of a bill no record places in a stream.
- `platform` and `item`: `Anthropic`, `OpenAI`, `Other model providers` by model; `Google Cloud` by lane or machine group; `Azure` by service; `GitHub` by repository.
- Sum every `kind` except `attributed_org_unknown`: that kind may be in a bill already, and adding it counts dollars twice.
- `Google Cloud` is `estimated` at list price, so read it as a close figure, not the invoice.

My project this week by platform:

```sql
SELECT platform, ROUND(SUM(usd), 2) AS usd
FROM `theorem-agent-infra.agent_spend.project_spend_daily_v`
WHERE project = 'BET' AND day >= DATE_SUB(CURRENT_DATE(), INTERVAL 6 DAY) AND kind != 'attributed_org_unknown'
GROUP BY platform ORDER BY usd DESC
```

Change `platform` in `GROUP BY` to `item` and filter `platform` to drill down: `platform = 'GitHub'` gives repositories, `platform IN ('Google Cloud', 'Azure')` gives lanes and machine groups, and the model platforms give models.

## Workflow and job cost: `project_actions_daily_v`

GitHub Actions by `project`, `repository`, `workflow`, `runner`, `outcome`, with `minutes` and `usd` at list price. It is the same money as the `GitHub` rows above counted another way, so never add the two views together.

```sql
SELECT repository, workflow, runner, outcome, SUM(minutes) AS minutes, ROUND(SUM(usd), 2) AS usd
FROM `theorem-agent-infra.agent_spend.project_actions_daily_v`
WHERE project = 'BET' AND day >= DATE_SUB(CURRENT_DATE(), INTERVAL 6 DAY)
GROUP BY 1, 2, 3, 4 ORDER BY usd DESC LIMIT 20
```

## Other questions

- One person's spend, how fresh each source is: the README section has the exact queries. Check freshness first when a number looks too low: a source whose last `day` is older than yesterday stopped loading.
- A colleague's machines show only as `People's machines`, never by name. Do not try to name them, and do not act on a colleague's workload.
