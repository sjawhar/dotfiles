# Taiga access: who grants it, and which accounts hold it

`gcp-taiga` is Anthropic's project, not ours. Nobody at Theorem can grant a role on it. Archana,
2026-07-14, to the same request this file exists to stop: "gcp-taiga is anthropic's thing, I can't
provision read/write to it"
(<https://theoremdev.slack.com/archives/C0A9GTRKQ7M/p1784079128767029>).

So never ask for `roles/artifactregistry.*` on `projects/gcp-taiga`, and never route a Taiga
request through Sami. He grants nothing there.

## Use an account that already has access

| Account | What it is for |
|---|---|
| `dev-sjawhar@theorem-agent-infra.iam.gserviceaccount.com` | the machine profile of the development boxes |
| `taiga-ci@theorem-agent-infra.iam.gserviceaccount.com` | continuous integration |
| `482450377695-compute@developer.gserviceaccount.com` | reading images only |
| `taiga-buildbox@autoformalization.iam.gserviceaccount.com` | Taiga environments; enabled on `strict-binary-equivalence-rocq-verified` |

Archana added the first three to the groups `anthropic-taiga-users@theorem.dev` and
`anthropic-taiga-engineers@theorem.dev` on 2026-10-19 ("ok added all three to both users and
engineers"). On 2026-10-20 she ruled out adding more: agent addresses are not historically on that
list, `taiga-ci` should use `taiga-buildbox` instead, and each new one has to be asked of Anthropic.
Sami accepted ("Oh, great, Ok I will use that"):
<https://theoremdev.slack.com/archives/D0C1ZEPEKCK/p1792423904680989>.

A host that is refused runs as the wrong account. Give it one of these, or impersonate one
(`--impersonate-service-account`); do not ask for a grant.

Measured 2026-10-31 04:48Z as `dev-sjawhar`: `gcloud artifacts docker images list
us-east1-docker.pkg.dev/gcp-taiga/theorem` lists images, and `docker manifest inspect` returns the
manifest of a `machcsl-proof-tasks` digest. The route works today.

## Two different systems

- **The image registry** (`us-east1-docker.pkg.dev/gcp-taiga/theorem`): Anthropic binds the two
  Google groups above on the repository. Membership is the whole of access, and Archana manages it
  in admin.google.com (Groups > the group > Add members).
- **Taiga environments** (jobs, problems, taiga.ant.dev): separate, and per environment, not per
  Docker repository. Org admins set it on Taiga's Access Control page.

## If one of the four genuinely will not do

Ask the person who holds the control, not Sami: Archana for group membership; Anthropic in
`#theorem-anthropic` for a binding or an environment. Bring the case for why no existing account
fits. Anthropic asks for a named human per change, so expect it to be slow.
