---
name: google-workspace
description: Use when reading, searching, uploading, downloading, sharing, or organizing files on Google Drive; reading or editing Google Docs/Sheets; reading Calendar; or reading, sending, replying to, and drafting Gmail (the write path is YubiKey-gated via an explicit GWS_WORK_SEND_OAUTH or GWS_PERSONAL_SEND_OAUTH wrapper). Also covers creating/suspending Workspace users and Google Group membership via GWS_WORK_ADMIN_OAUTH. Triggers on "google drive", "gdrive", "workspace", "send an email", "reply to", "draft an email", Gmail/Drive/Docs URLs (drive.google.com, docs.google.com), file IDs, or requests to find, list, export, share, email, or manage cloud documents and spreadsheets.
---

# Google Workspace (gws)

Manage Google Drive (and other Workspace APIs) via the `gws` CLI. All output is structured JSON. The dotfiles `gws` shim routes authentication by identity and operation (see the Auth section); there is NO MCP server mode (upstream removed the `mcp` command).

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

## Auth — account-scoped credentials

`gws` on PATH is the dotfiles shim (`~/.dotfiles/shims/gws`). It has five credential keys. Use the task, not a guessed key name, to choose one.

| Task | Credential | Tier | How |
|------|-----------|------|-----|
| Read/search Drive, Docs, Sheets, Calendar, or Gmail as work | `GWS_WORK_READ_OAUTH` | agent | Run `gws …`; the shim obtains the key when needed. On the EC2 devbox, work reads still use the keyless `google-user-token` broker instead. |
| Read/search those APIs as personal | `GWS_PERSONAL_READ_OAUTH` | agent | Run `GWS_ACCOUNT=personal gws …`; the shim obtains the key when needed. |
| Send Gmail, reply in-thread, or create/send drafts as work | `GWS_WORK_SEND_OAUTH` | human | Run `secrets GWS_WORK_SEND_OAUTH -- gws gmail …`; one YubiKey touch per session. |
| Send Gmail, reply in-thread, or create/send drafts as personal | `GWS_PERSONAL_SEND_OAUTH` | human | Run `secrets GWS_PERSONAL_SEND_OAUTH -- gws gmail …`; one YubiKey touch per session. |
| Create/suspend Workspace users or manage Google Group membership | `GWS_WORK_ADMIN_OAUTH` | human | Use it in an explicit `secrets GWS_WORK_ADMIN_OAUTH -- <Admin Directory command>` wrapper; one YubiKey touch per session. The normal shim routes do not select this key for `gws`. |

### Select an identity

`GWS_ACCOUNT` chooses the read identity and its credential store. It accepts only `work` or `personal` and defaults to `work`; any other value is an error. The stores are separate:

- work: `~/.config/gws/work`
- personal: `~/.config/gws/personal`

```bash
# Work is the default
gws drive files list --params '{"pageSize": 10}'

# Select personal for this command only
GWS_ACCOUNT=personal gws drive files list --params '{"pageSize": 10}'
```

Reading never needs a `secrets` wrapper and never needs a YubiKey touch. On the EC2 devbox, work reads take the unchanged keyless broker path; otherwise the shim re-executes under `secrets` to fetch the selected account's agent-tier read credential. The shim materializes the credential in a private temporary file and selects that account's store before starting the real CLI.

### Send Gmail — always name the human credential

Sending never follows the automatic read path. Name exactly one send credential in the `secrets` wrapper; the named credential selects the sending identity and its store, regardless of `GWS_ACCOUNT`. Setting both send credentials is an error.

```bash
# Work: new message
secrets GWS_WORK_SEND_OAUTH -- gws gmail +send \
  --to 'a@b.com' --cc 'peter@trajectorylabs.com' --subject 'Subject' --body 'Body text'

# Personal: reply in-thread (handles In-Reply-To/References + threadId automatically)
secrets GWS_PERSONAL_SEND_OAUTH -- gws gmail +reply \
  --message-id '<GMAIL_MSG_ID>' --body 'Reply text'

# --html for HTML body, -a PATH to attach, --dry-run to inspect the request without sending
# Stage a work draft instead of sending, then send it later by draft id
secrets GWS_WORK_SEND_OAUTH -- gws gmail +reply --message-id '<GMAIL_MSG_ID>' --body 'text' --draft
secrets GWS_WORK_SEND_OAUTH -- gws gmail users drafts send \
  --params '{"userId":"me"}' --json '{"id":"<DRAFT_ID>"}'
```

### `gws auth` manages a store, not an operation credential

`gws auth …` receives no injected credential, because it creates and inspects credentials. It still honours `GWS_ACCOUNT`: `GWS_ACCOUNT=personal gws auth status` uses the personal store. The shim defaults `GOOGLE_WORKSPACE_CLI_CONFIG_DIR` to the selected account's store but preserves a value the caller already supplied.

### Explicit CLI credential overrides

Preset `GOOGLE_WORKSPACE_CLI_TOKEN` (a raw access token) or `GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE` (an authorized-user JSON) and the shim execs the real CLI untouched. This takes priority over account selection and all normal shim credential routing.

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
