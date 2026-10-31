# Accounts with Taiga registry and environment access

Four service accounts can pull from the Taiga image registry and reach Taiga environments. Use one of these instead of asking for a new account.

| Account | What it is for | Added |
|---|---|---|
| `dev-sjawhar@theorem-agent-infra.iam.gserviceaccount.com` | the machine profile of the development boxes | 2026-10-19 |
| `taiga-ci@theorem-agent-infra.iam.gserviceaccount.com` | continuous integration | 2026-10-19 |
| `482450377695-compute@developer.gserviceaccount.com` | reading images only | 2026-10-19 |
| `taiga-buildbox@autoformalization.iam.gserviceaccount.com` | already enabled on the environment | before 2026-10-19 |

The first two were added to the groups `anthropic-taiga-users@theorem.dev` and `anthropic-taiga-engineers@theorem.dev` on 2026-10-19.

Agent email addresses were not historically added to that list, so an agent identity that is refused is expected; use one of the four accounts above.

Source: Sami's direct messages with Archana, 2026-10-19 and 2026-10-20, <https://theoremdev.slack.com/archives/D0C1ZEPEKCK/p1792423904680989>.
