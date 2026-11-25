---
name: reading-spend
description: "Use when asked what a project, stream, repository, workflow, VM, model or person costs, or whether spend is worth it: cloud, CI, GitHub Actions, Azure, OpenAI, Anthropic dollars. Also before asking anyone for GitHub org or billing access to see costs: the data is already in BigQuery."
---

# Reading spend

All of Theorem's spend is in the BigQuery dataset `theorem-app-spend.agent_spend`, which every agent identity can read: do not ask a person for GitHub organization, billing or console access to see costs. The old site's `theorem-agent-infra.agent_spend` has the same tables but stopped loading on 2026-11-23 (dispatch://INFRA-4743 moved it, dispatch://INFRA-4416 deletes it), so a query of it returns too little and no error. The full guide, with a query per question and the meaning of every column, is theorem-labs/agent-infra `spend/README.md`, "For agents: reading your project's spend".

## Run a query

```bash
bq query --project_id=theorem-agent-infra --use_legacy_sql=false --maximum_bytes_billed=5000000000 '<SQL>'
```

- Always pass `--maximum_bytes_billed=5000000000` (5 GB): nothing else stops a large query, and a full scan of `otel_raw` reads many GB.
- On a table you have not queried before, run with `--dry_run` first: it prints the bytes it would read and bills nothing.
- Refused `bigquery.jobs.create` in `theorem-agent-infra`: run the same query with `--project_id=autoformalization` or `--project_id=theorem-research`. If no project lets you run it, or the table itself says "Access Denied" (your identity is not a reader), tell the cost-tracking lead instead of looking for another way in: comment on the cost-tracking issue in Dispatch, the issue tracker (`skill://dispatch`). Find its key with `dispatch search --query 'cost tracking per project'`, then run `dispatch comment --issue <KEY> --body '<host, identity, command, error>'`.

## Start from `project_spend_daily_v`

One row per UTC `day`, `project`, `platform`, `item` and `kind`, with `usd`.

- `project`: `BET`, `OSSV`, `LINUX`, `INFRA`, `LNB`, `Rocq speed`, or a repository's own name. Not a project: `(no project)` (bill remainders a record cannot place yet), the catch-all labels `Agents, no stream`, `untagged` and Azure's `Untagged`, `People's machines` (colleagues' own machines on Google Cloud and Azure) and `No owner found` (cloud spend nobody claims).
- `platform` and `item`: `Anthropic`, `OpenAI`, `Other model providers` by model; `Google Cloud` by lane (a VM's lane label or a GKE pod's namespace), else by machine group (the VM's name less its trailing number); `Azure` by service; `GitHub` by repository.
- `kind`, ours: `attributed` (model dollars the harnesses reported), `estimated` (Google Cloud from measured usage, at list price less its share of discounts), `billed` (bill rows placed on a project: a mapped Google service, Azure's VMs, disks and listed resource groups, GitHub's bill), and the signed remainders `billed_unattributed` (Anthropic, Azure), `cost_report_unattributed` (OpenAI) and `estimated_unattributed` (Google Cloud): our part of each bill that no record places.
- `kind`, not ours: `colleague` (colleagues' share of an Anthropic organization's bill, one row per day and organization), `caller_unknown` (bill dollars no report can assign), `other_project` (OpenAI use by keys not ours), `overhead` (Google Support, GitHub's organization-wide items such as seats), `credit` (Google's promotional credit). `not_billed` is the Anthropic spend of the organizations Theorem is not charged for (`Theorem - HDO` and `Theorem - HDO V2`, the spend README's config `unbilledAnthropicOrganizations`); `attributed_org_unknown` is model calls whose organization is unknown, which a bill may already hold.
- Column `provisional` is TRUE on an Anthropic row whose share of a bill can still move, until both of that day's reports were read at least 24 hours after the day ended.
- The company's charged total: every row except kinds `not_billed` and `attributed_org_unknown`.
- Ours: also leave out kinds `colleague`, `caller_unknown`, `other_project`, `overhead`, `credit`, and projects `People's machines` and `No owner found`.
- `Google Cloud` rows count dollars before Google's promotional credit; one signed `credit` row a day, in `(no project)`, brings the platform back to the bill Google charges. The bill less Support (the `overhead` row) less the estimate is split, signed, between ours, `People's machines` and `No owner found`, so a remainder row can be below 0.
- Today and yesterday still grow: OpenAI posts a day's costs during that day and the next.

A project's week by platform:

```sql
SELECT platform, ROUND(SUM(usd), 2) AS usd
FROM `theorem-app-spend.agent_spend.project_spend_daily_v`
WHERE project = 'BET' AND day >= DATE_SUB(CURRENT_DATE(), INTERVAL 6 DAY)
  AND kind NOT IN ('attributed_org_unknown', 'not_billed', 'colleague', 'caller_unknown', 'other_project', 'overhead', 'credit')
GROUP BY platform ORDER BY usd DESC
```

To drill down, group by `item` and filter `platform`: `'GitHub'` gives repositories, `IN ('Google Cloud', 'Azure')` gives lanes and machine groups, a model platform gives models.

## Workflow and job cost: `project_actions_daily_v`

GitHub Actions by `project`, `repository`, `workflow`, `runner` and `outcome`, with `minutes` and `usd` at list price. It is the same money as the `GitHub` rows above counted another way: never add the two views. `outcome` is NULL on rows for 2026-10-28 and 10-29, which hold every outcome of those days together.

```sql
SELECT repository, workflow, runner, outcome, SUM(minutes) AS minutes, ROUND(SUM(usd), 2) AS usd
FROM `theorem-app-spend.agent_spend.project_actions_daily_v`
WHERE project = 'BET' AND day >= DATE_SUB(CURRENT_DATE(), INTERVAL 6 DAY)
GROUP BY 1, 2, 3, 4 ORDER BY usd DESC LIMIT 20
```

## Other questions

- One person's spend, and each source's freshness: the README section has the queries. Before you report a number, run the freshness query: a source whose last `day` is before yesterday stopped loading, so a number that covers later days is too low.
- Never name a machine that is not ours, as the "Ours" line above defines it: a machine in `People's machines` (a colleague's own) or `No owner found`. Never run a command that logs in to such a machine or changes it (stop, resize, delete, relabel). The views show colleagues only as `People's machines` and `colleague`; the tables `cloud_cost_estimated`, `cloud_cost_today` and `billed_usage_caller` name them, so do not read names from those tables, and treat colleagues' rows as out of scope.
