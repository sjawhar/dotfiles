---
name: google-workspace
description: Use when reading, searching, uploading, downloading, sharing, or organizing files on Google Drive; reading or editing Google Docs/Sheets; reading Calendar; or reading, sending, replying to, and drafting Gmail (the write path is YubiKey-gated via GMAIL_OAUTH_CREDENTIALS). Also covers creating/suspending Workspace users and Google Group membership via GOOGLE_ADMIN_OAUTH. Triggers on "google drive", "gdrive", "workspace", "send an email", "reply to", "draft an email", Gmail/Drive/Docs URLs (drive.google.com, docs.google.com), file IDs, or requests to find, list, export, share, email, or manage cloud documents and spreadsheets.
---

# Google Workspace (gws)

Manage Google Drive (and other Workspace APIs) via the `gws` CLI. All output is structured JSON. Auth is automatic on admin devboxes (machine identity — see the Auth section); there is NO MCP server mode (upstream removed the `mcp` command).

## Docs & Sheets Formatting Rules

When writing to Docs/Sheets that humans read, formatting mistakes are recurring and costly — follow these without exception:

- **Native structure, not lookalike characters.** Use real bullet lists via the API (`createParagraphBullets`), never literal `•`/`◦` characters pasted into text.
- **Hyperlinked display text, not bare URLs.** Write "transcript" or "link" with a hyperlink, never the full Drive URL inline.
- **Match adjacent content.** Before inserting rows/cells/sections, inspect the formatting of existing neighbors and replicate it (text style, hyperlink pattern, column conventions). Table cells use normal text style — never heading styles (Header 2 in a table cell renders huge).
- **Never overwrite concurrent human edits.** Sami often edits shared docs live while you work — re-read the target range before writing and merge around his changes.
- **Check the audience before writing** (see CLAUDE.md Audience Boundaries): customer-shared files live in the shared drive, never My Drive; internal ops detail never goes into customer-visible docs/tabs.

## No MCP Mode

Upstream removed the `gws mcp` command (CHANGELOG: "Remove `mcp` command"); `skill_mcp` and any `mcp:` frontmatter for gws will fail with "Connection closed". Use the CLI below — same operations, same JSON.

## CLI Quick Start

```bash
# List files
gws drive files list --params '{"pageSize": 10}'

# Search files
gws drive files list --params '{"q": "name contains \"report\"", "pageSize": 10}'

# Download a file
gws drive files get --params '{"fileId": "FILE_ID", "alt": "media"}' -o ./output.pdf

# Upload a file
gws drive files create --json '{"name": "report.pdf"}' --upload ./report.pdf

# Share a file
gws drive permissions create \
  --params '{"fileId": "FILE_ID"}' \
  --json '{"role": "reader", "type": "user", "emailAddress": "user@example.com"}'
```

## CLI Syntax

```
gws <service> <resource> <method> [flags]
```

| Flag | Description |
|------|-------------|
| `--params '{...}'` | URL/query parameters |
| `--json '{...}'` | Request body |
| `--upload <PATH>` | Upload file (multipart) |
| `-o, --output <PATH>` | Save response to file |
| `--page-all` | Auto-paginate (NDJSON) |
| `--dry-run` | Preview without calling API |

**stdout is pure JSON; never merge stderr into it.** Advisory output (`Using keyring backend: keyring`, `Tip: ...`) goes to **stderr**, on both the automatic and the credential path. Verified: with stderr discarded, stdout parses as JSON with zero advisory lines. So pipe stdout straight into `jq`/`json.load`, and if you want the advisories, capture them separately.

```bash
gws … | jq .                      # correct
gws … 2>/dev/null | jq .          # correct, advisories dropped
gws … >out.json 2>err.txt         # correct, both kept and separate
gws … 2>&1 | jq .                 # WRONG: mixes advisories into the JSON and the parse fails
```

Habitually appending `2>&1` is the single easiest way to break a gws pipeline, and the resulting error looks like a gws bug rather than a redirection mistake.

## Discovering API Methods

```bash
gws drive --help                    # List all resources
gws schema drive.files.list         # Inspect params/types for a method
```

Use `gws schema` output to build `--params` and `--json` flags.

## Common Drive Queries (q parameter)

```
name contains 'budget'                    # Name search
mimeType = 'application/pdf'              # By type
modifiedTime > '2025-01-01T00:00:00'      # Recently modified
'FOLDER_ID' in parents                    # Files in folder
trashed = false                           # Exclude trash
sharedWithMe = true                       # Shared with me
```

Combine with `and`: `name contains 'report' and mimeType = 'application/pdf'`

## Auth — three credentials, three jobs

There are **three** distinct Google auth paths on this box. Pick by what you're doing. The two `*_OAUTH*` keys are human-tier (secretsd, one YubiKey touch per use); the third is automatic.

**The two human-tier keys are the same *kind* of object** — both are OAuth 2.0 authorized-user bundles (`{client_id, client_secret, refresh_token}`), both used the same way (POST a `refresh_token` grant → 1-hour access token → call an API). They are **not** interchangeable, but the difference is in the grant, not the format: they were consented under **different OAuth clients**, carry **disjoint scopes**, and act as **different identities**. `GMAIL_OAUTH_CREDENTIALS` acts as `sami@trajectorylabs.net` over the mailbox (`gmail.compose` + `gmail.readonly`, + userinfo/openid); `GOOGLE_ADMIN_OAUTH` carries only the three `admin.directory.*` scopes and no mailbox identity. Each 403s on the other's job.

> **Naming caveat (known inconsistency):** the secretsd keys are `GMAIL_OAUTH_CREDENTIALS` (Gmail send/reply/draft) and `GOOGLE_ADMIN_OAUTH` (Admin Directory). The word order differs (`GMAIL_OAUTH` vs `GOOGLE_..._OAUTH`) and neither says what it can actually do. Do **not** guess or abbreviate them — `GMAIL_AUTH_CREDENTIALS`, `GOOGLE_ADMIN_OAUTH_CREDENTIALS`, etc. are all wrong and will fail. Copy the exact key from `secrets list`. If these are ever renamed, make them parallel (e.g. `GWS_GMAIL_WRITE_OAUTH` / `GWS_ADMIN_DIRECTORY_OAUTH`) and update this skill + the onboarding skill together.

| Task | Credential | How |
|------|-----------|-----|
| Read/search Drive, Docs, Sheets, Calendar; **read** Gmail | none (automatic) | just run `gws …` — the shim mints a DWD token for the box owner |
| **Send** Gmail, reply in-thread, create drafts | `GMAIL_OAUTH_CREDENTIALS` (human tier) | `secrets GMAIL_OAUTH_CREDENTIALS -- gws gmail +send\|+reply …` |
| Create/suspend Workspace users, manage Group membership | `GOOGLE_ADMIN_OAUTH` (human tier) | mint token → call Admin SDK Directory REST (see the onboarding skill) |

### 1. Automatic (default) — read-only Workspace + Gmail read
`gws` on PATH is the dotfiles shim (`~/.dotfiles/shims/gws`). On the EC2 devbox (detected locally via DMI), with no special env var set it authenticates unattended via machine identity: it mints a domain-wide-delegation token for the box owner through `google-user-token` (instance role → WIF → native google-auth user impersonation; no stored keys). Just run `gws …` — nothing to touch. On non-EC2 machines (laptops) there is no instance role, so the shim passes through untouched and real gws uses the human `~/.config/gws` credentials from `gws auth login`.

- **On the devbox, do NOT run `gws auth login`/`export` for day-to-day auth** — the broker handles it there. The login flow is the normal auth on laptops, and on the devbox exists only to re-provision the Gmail write credential (below).
- **DWD scopes granted:** `drive`, `documents`, `spreadsheets`, `presentations`, `calendar`, `gmail.readonly`. This path can read Gmail but **cannot send** (the grant omits `gmail.send`). Other services (tasks, chat, forms, keep, meet, people) fail with insufficient-scope until three things are extended together: the Admin-console DWD grant, the `default_scopes` list in the broker's config secret, and the enabled-API list on the GCP project (both Pulumi-managed).
- **If `gws` resolves to the raw binary** (stale session PATH), call the shim by absolute path: `/home/ubuntu/.dotfiles/shims/gws …`. On EC2, broker failures are loud by design; there is no silent fallback to human credentials.

### 2. `GMAIL_OAUTH_CREDENTIALS` — writing Gmail: send, reply, draft (human tier, YubiKey-gated)
Writing to the mailbox is deliberately impossible on the automatic path. Route it through this key and the **same gws shim** — do NOT hand-roll OAuth in python (that just reinvents what the shim does). When `GMAIL_OAUTH_CREDENTIALS` is set in the env, the shim writes the `{client_id, client_secret, refresh_token}` bundle to a 0600 tempfile, points real gws at it via `GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE`, runs, and cleans up.

```bash
# New message
secrets GMAIL_OAUTH_CREDENTIALS -- gws gmail +send \
  --to 'a@b.com' --cc 'peter@trajectorylabs.com' --subject 'Subject' --body 'Body text'
# Reply in-thread (handles In-Reply-To/References + threadId automatically — prefer this over building MIME by hand)
secrets GMAIL_OAUTH_CREDENTIALS -- gws gmail +reply --message-id '<GMAIL_MSG_ID>' --body 'Reply text'
# --html for HTML body, -a PATH to attach, --dry-run to inspect the request without sending
# Stage a draft instead of sending, then send it later by draft id
secrets GMAIL_OAUTH_CREDENTIALS -- gws gmail +reply --message-id '<GMAIL_MSG_ID>' --body 'text' --draft
secrets GMAIL_OAUTH_CREDENTIALS -- gws gmail users drafts send \
  --params '{"userId":"me"}' --json '{"id":"<DRAFT_ID>"}'
```

- **Scopes carried:** `gmail.compose`, `gmail.readonly`, `userinfo.email`, `userinfo.profile`, `openid`. Verify at any time with a `refresh_token` grant against `oauth2.googleapis.com/token` and read the returned `scope` field (print only that field, never the token).
- **`+reply` works in a single call.** It fetches the target message to build `In-Reply-To`/`References`/`threadId`, which needs read scope; `gmail.readonly` covers it. Prefer `+reply` over assembling MIME by hand — hand-rolling threading headers is easy to get subtly wrong.
- **`gmail.compose` subsumes `gmail.send`,** which is why `gmail.send` is not in the list. Per Google's [`users.drafts.create` reference](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.drafts/create), draft creation requires one of `mail.google.com`, `gmail.modify`, or `gmail.compose`; `gmail.compose` is the narrowest and is the only one scoped to drafts rather than the whole mailbox. Note it also permits *deleting* drafts, so it is not purely additive over send.
- **Re-provisioning the credential** (rare): `gws auth login --scopes https://www.googleapis.com/auth/gmail.compose,https://www.googleapis.com/auth/gmail.readonly` (tunnel the printed localhost port), then `gws auth export --unmasked` (default output MASKS `client_secret`/`refresh_token` as `xxxx...yyyy` — storing the masked form breaks refresh), compact to a single line, store via `secrets edit-human GMAIL_OAUTH_CREDENTIALS` (multi-line values are rejected), then `gws auth logout` and shred the plaintext.

### 3. `GOOGLE_ADMIN_OAUTH` — Workspace admin (human tier, YubiKey-gated)
A separate OAuth authorized-user bundle (same `{client_id, client_secret, refresh_token}` format as the Gmail one, but a **different OAuth client** and disjoint scopes) carrying ONLY the Admin SDK Directory scopes (`admin.directory.user`, `admin.directory.group`, `admin.directory.group.member`) and no mailbox identity. Used for creating/suspending `@trajectorylabs.net` accounts and managing Google Group membership — the contractor-onboarding path. **You call the Admin SDK REST API directly with it, not through gws** — not because it's a different *kind* of credential (the gws shim would accept it via `GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE` just like the Gmail one), but because gws has no admin-directory command surface to route through. Mint a token via the `refresh_token` grant and hit the REST endpoints.

```bash
# token → Admin Directory REST
secrets GOOGLE_ADMIN_OAUTH -- python3 -c 'mint refresh_token grant → POST admin.googleapis.com/admin/directory/v1/users ...'
```

Full recipe (account creation, recovery email, group add, the propagation-lag re-read) lives in the **onboarding skill** (`core-ops/onboarding` → *Google Admin over the API*). Do not duplicate the mechanics here; this row exists so the three creds are distinguishable in one place.

### Escape hatches (bypass the broker)
Preset `GOOGLE_WORKSPACE_CLI_TOKEN` (a raw access token) or `GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE` (an authorized-user JSON) and the shim execs straight through untouched. This is exactly the mechanism path 2 uses under the hood — and it's why the credential *format* is the same across all the human-tier keys: any of them could ride this hatch; the difference is only which scopes/identity the grant carries and whether the target API has a gws command surface at all.

## Gotchas

- **`fields` parameter required** for some methods (`about.get`, `comments.*`). Check `gws schema` if you get empty responses.
- **Sheets ranges use `!`** which bash interprets as history expansion. Always single-quote: `'Sheet1!A1:C10'`
- **OAuth scope limits**: Unverified apps (testing mode) limited to ~25 scopes. Use `-s drive` not `-s all` for personal use.
- **Google Docs/Sheets aren't directly downloadable** — use `files.export` with a target MIME type (`application/pdf`, `text/csv`), not `files.get`.
- **Pre-v1.0**: `gws` is under active development. Expect occasional breaking changes in flags or MCP tool names.

## Full API Reference

For complete API coverage, see the upstream skills:

- **[gws-drive](https://github.com/googleworkspace/cli/blob/main/skills/gws-drive/SKILL.md)** — All Drive resources and methods
- **[gws-shared](https://github.com/googleworkspace/cli/blob/main/skills/gws-shared/SKILL.md)** — Auth, global flags, security rules
- **[Skills Index](https://github.com/googleworkspace/cli/blob/main/docs/skills.md)** — All services, helpers, and recipes

```bash
# Fetch any upstream skill for detailed reference
curl -sL https://raw.githubusercontent.com/googleworkspace/cli/main/skills/gws-drive/SKILL.md
```
