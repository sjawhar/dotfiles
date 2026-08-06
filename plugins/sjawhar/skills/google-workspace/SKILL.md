---
name: google-workspace
description: Use when reading, searching, uploading, downloading, sharing, or organizing files on Google Drive; reading or editing Google Docs/Sheets; reading Calendar; or reading AND sending Gmail (the send path is YubiKey-gated via GMAIL_OAUTH_CREDENTIALS). Also covers creating/suspending Workspace users and Google Group membership via GOOGLE_ADMIN_OAUTH. Triggers on "google drive", "gdrive", "workspace", "send an email", "reply to", Gmail/Drive/Docs URLs (drive.google.com, docs.google.com), file IDs, or requests to find, list, export, share, email, or manage cloud documents and spreadsheets.
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

> **Naming caveat (known inconsistency):** the secretsd keys are `GMAIL_OAUTH_CREDENTIALS` (Gmail *send*) and `GOOGLE_ADMIN_OAUTH` (Admin Directory). The word order differs (`GMAIL_OAUTH` vs `GOOGLE_..._OAUTH`) and neither says what it can actually do. Do **not** guess or abbreviate them — `GMAIL_AUTH_CREDENTIALS`, `GOOGLE_ADMIN_OAUTH_CREDENTIALS`, etc. are all wrong and will fail. Copy the exact key from `secrets list`. If these are ever renamed, make them parallel (e.g. `GWS_GMAIL_SEND_OAUTH` / `GWS_ADMIN_DIRECTORY_OAUTH`) and update this skill + the onboarding skill together.

| Task | Credential | How |
|------|-----------|-----|
| Read/search Drive, Docs, Sheets, Calendar; **read** Gmail | none (automatic) | just run `gws …` — the shim mints a DWD token for the box owner |
| **Send** Gmail | `GMAIL_OAUTH_CREDENTIALS` (human tier) | `secrets GMAIL_OAUTH_CREDENTIALS -- gws gmail +send …` |
| Create/suspend Workspace users, manage Group membership | `GOOGLE_ADMIN_OAUTH` (human tier) | mint token → call Admin SDK Directory REST (see the onboarding skill) |

### 1. Automatic (default) — read-only Workspace + Gmail read
`gws` on PATH is the dotfiles shim (`~/.dotfiles/shims/gws`). With no special env var set it authenticates unattended via machine identity: it mints a domain-wide-delegation token for the box owner through `google-user-token` (instance role → WIF → native google-auth user impersonation; no stored keys). Just run `gws …` — nothing to touch.

- **Do NOT run `gws auth login`/`export` for day-to-day auth** — the broker handles it. The login flow exists only to re-provision the Gmail-send credential (below).
- **DWD scopes granted:** `drive`, `documents`, `spreadsheets`, `presentations`, `calendar`, `gmail.readonly`. This path can read Gmail but **cannot send** (the grant omits `gmail.send`). Other services (tasks, chat, forms, keep, meet, people) fail with insufficient-scope until three things are extended together: the Admin-console DWD grant, the `default_scopes` list in the broker's config secret, and the enabled-API list on the GCP project (both Pulumi-managed).
- **If `gws` resolves to the raw binary** (stale session PATH), call the shim by absolute path: `/home/ubuntu/.dotfiles/shims/gws …`. Failures are loud by design; there is no silent fallback to human credentials.

### 2. `GMAIL_OAUTH_CREDENTIALS` — sending Gmail (human tier, YubiKey-gated)
Sending is deliberately impossible on the automatic path. Route sends through this key and the **same gws shim** — do NOT hand-roll OAuth in python (that just reinvents what the shim does). When `GMAIL_OAUTH_CREDENTIALS` is set in the env, the shim writes the `{client_id, client_secret, refresh_token}` bundle to a 0600 tempfile, points real gws at it via `GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE`, runs, and cleans up.

```bash
# New message
secrets GMAIL_OAUTH_CREDENTIALS -- gws gmail +send \
  --to 'a@b.com' --cc 'peter@trajectorylabs.com' --subject 'Subject' --body 'Body text'
# Reply in-thread (handles In-Reply-To/References + threadId automatically — prefer this over building MIME by hand)
secrets GMAIL_OAUTH_CREDENTIALS -- gws gmail +reply --message-id '<GMAIL_MSG_ID>' --body 'Reply text'
# --html for HTML body, -a PATH to attach, --draft to stage instead of send
```

- **Scope is send-only.** The credential carries exactly `gmail.send userinfo.email userinfo.profile openid` — no read scope. So `getProfile`, `users messages list/get`, `+triage`, `+read` all **403 (insufficient scopes)** under this key. That 403 is expected, not a failure: send-via-`GMAIL_OAUTH_CREDENTIALS`, read-via-plain-`gws`.
- **You still need plain `gws` (path 1) to read** the thread you're replying to — fetch the target message ID / headers first on the automatic path, then send on this one.
- **Re-provisioning the credential** (rare): `gws auth login --scopes https://www.googleapis.com/auth/gmail.send` (tunnel the printed localhost port), then `gws auth export --unmasked` (default output MASKS `client_secret`/`refresh_token` as `xxxx...yyyy` — storing the masked form breaks refresh), compact to a single line, store via `secrets edit-human GMAIL_OAUTH_CREDENTIALS` (multi-line values are rejected), then `gws auth logout` and shred the plaintext.

### 3. `GOOGLE_ADMIN_OAUTH` — Workspace admin (human tier, YubiKey-gated)
A separate `{client_id, client_secret, refresh_token}` bundle carrying ONLY the Admin SDK Directory scopes (`admin.directory.user`, `admin.directory.group`, `admin.directory.group.member`). Used for creating/suspending `@trajectorylabs.net` accounts and managing Google Group membership — the contractor-onboarding path. This one is **not** a gws credential: mint a token via the `refresh_token` grant and call the Admin SDK REST API directly.

```bash
# token → Admin Directory REST
secrets GOOGLE_ADMIN_OAUTH -- python3 -c 'mint refresh_token grant → POST admin.googleapis.com/admin/directory/v1/users ...'
```

Full recipe (account creation, recovery email, group add, the propagation-lag re-read) lives in the **onboarding skill** (`core-ops/onboarding` → *Google Admin over the API*). Do not duplicate the mechanics here; this row exists so the three creds are distinguishable in one place.

### Escape hatches (bypass the broker)
Preset `GOOGLE_WORKSPACE_CLI_TOKEN` (a raw access token) or `GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE` (a credentials JSON) and the shim execs straight through untouched. This is exactly the mechanism path 2 uses under the hood.

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
