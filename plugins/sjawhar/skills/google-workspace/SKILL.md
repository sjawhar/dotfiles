---
name: google-workspace
description: Use when reading, searching, uploading, downloading, sharing, or organizing files on Google Drive. Also for Google Docs, Sheets, Gmail, or Calendar access. Triggers on "google drive", "gdrive", "workspace", Drive/Docs URLs (drive.google.com, docs.google.com), file IDs, or requests to find, list, export, share, or manage cloud documents and spreadsheets.
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

## Auth (automatic on admin devboxes)

`gws` on PATH is the dotfiles shim (`~/.dotfiles/shims/gws`), which authenticates
unattended via machine identity: it mints a domain-wide-delegation token for the box
owner through `google-user-token` (instance role → WIF → native google-auth user
impersonation; no stored keys). The broker reads its service account, default scopes,
and federation config from an AWS Secrets Manager entry named in the script; it uses
no local configuration files.

- **Do NOT run `gws auth login`/`export` for day-to-day auth** — the broker handles
  it. The login flow exists only to provision the gated Gmail-send credential below.
- **Available scopes** (the DWD grant): `drive`, `documents`, `spreadsheets`,
  `calendar`, `gmail.readonly`. Other services (slides, tasks, chat, forms, keep,
  meet, people) fail with insufficient-scope until the Admin-console grant is
  extended — update that grant and the `default_scopes` list in the broker's config
  secret together.
- **Sending Gmail is deliberately impossible unattended** (the grant omits
  `gmail.send`). The gated path (one YubiKey touch per command):
  `secrets GMAIL_OAUTH_CREDENTIALS -- gws gmail +send --to a@b --subject S --body B`.
  The credential exists only YubiKey-encrypted in the human tier. To re-provision:
  `gws auth login --scopes https://www.googleapis.com/auth/gmail.send` (tunnel the
  printed localhost port), then `gws auth export --unmasked` (default output MASKS
  `client_secret` and `refresh_token` as `xxxx...yyyy` — storing it breaks refresh),
  compact to a single line, store via `secrets edit-human GMAIL_OAUTH_CREDENTIALS`
  (multi-line values are rejected), then `gws auth logout` and shred the plaintext.
- **Escape hatches** (both bypass the broker): preset `GOOGLE_WORKSPACE_CLI_TOKEN`
  (an access token) or `GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE` (a credentials JSON).
- **If `gws` resolves to the raw binary** (stale session PATH), call the shim by
  absolute path: `/home/ubuntu/.dotfiles/shims/gws …` — it works from any
  environment. Failures are loud by design; there is no silent fallback to human
  credentials.

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
