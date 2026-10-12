---
name: google-workspace
description: Use when reading, searching, uploading, downloading, sharing, or organizing files on Google Drive; reading or editing Google Docs/Sheets/Slides; reading Calendar; across Sami's personal, theorem, and trajectory Google profiles. Also covers creating/suspending Workspace users and Google Group membership via GWS_WORK_ADMIN_OAUTH (trajectory only). Triggers on "google drive", "gdrive", "workspace", Gmail/Drive/Docs URLs (drive.google.com, docs.google.com), file IDs, or requests to find, list, export, share, or manage cloud documents, spreadsheets, and slides. Mail — reading, sending, replying, drafts, attachments — goes through the `mailbox` CLI, not this skill.
---

# Google Workspace (gws)

Manage Google Drive (and other Workspace APIs) via the `gws` CLI. All output is structured JSON. The dotfiles `gws` shim routes authentication by profile (see the Auth section); there is NO MCP server mode (upstream removed the `mcp` command).

## Docs & Sheets Formatting Rules

When writing to Docs/Sheets that humans read, formatting mistakes are recurring and costly — follow these without exception:

- **Native structure, not lookalike characters.** Use real bullet lists via the API (`createParagraphBullets`), never literal `•`/`◦` characters pasted into text.
- **Hyperlinked display text, not bare URLs.** Write "transcript" or "link" with a hyperlink, never the full Drive URL inline.
- **Match adjacent content.** Before inserting rows/cells/sections, inspect the formatting of existing neighbors and replicate it (text style, hyperlink pattern, column conventions). Table cells use normal text style — never heading styles (Header 2 in a table cell renders huge).
- **Never overwrite concurrent human edits.** Sami often edits shared docs live while you work — re-read the target range before writing and merge around his changes.
- **Audience boundaries:** follow `AGENTS.md`.

## No MCP Mode

**Gotcha:** MCP mode was removed upstream; `gws` CLI is the only path.

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
GWS_ACCOUNT=personal gws drive --help   # List all resources (any profile works)
gws schema drive.files.list             # Inspect params/types for a method; needs no profile
```

Use `gws schema` output to build `--params` and `--json` flags.

## Auth — profile-scoped credentials

`gws` on PATH is the dotfiles shim (`~/.dotfiles/shims/gws`). `GWS_ACCOUNT` is required on
every call except `gws schema`, which reads Google's public discovery documents, and `gws auth`
with a caller-set `GOOGLE_WORKSPACE_CLI_CONFIG_DIR`. There is no default identity: a bare
`gws drive …` fails and lists the profiles.

| Profile | Account | Credential | Store |
|---------|---------|-----------|-------|
| `personal` | sami.n.jawhar@gmail.com | `GWS_PERSONAL_READ_OAUTH` | `~/.config/gws/personal` |
| `theorem` | Sami's Theorem Google account | `GWS_THEOREM_READ_OAUTH` (not provisioned yet: waits on Theorem's OAuth client, so `GWS_ACCOUNT=theorem` fails with `secret not found`) | `~/.config/gws/theorem` |
| `trajectory` | sami@trajectorylabs.net | `GWS_TRAJECTORY_READ_OAUTH` | `~/.config/gws/trajectory` |

All three keys are secretsd agent-tier and need no YubiKey touch. Despite the `READ` in the key
names, the grants are read-write for Drive, Docs, Sheets, Slides, and Calendar, and read-only for
Gmail — gws can still read Gmail (`gmail.readonly`), but mail work (reading, sending, replying,
drafts, attachments) goes through the `mailbox` CLI, not gws.

| Task | Credential | Tier | How |
|------|-----------|------|-----|
| Read/search Drive, Docs, Sheets, Slides, Calendar, or Gmail as a given profile | `GWS_<PROFILE>_READ_OAUTH` | agent | Run `GWS_ACCOUNT=<profile> gws …`; the shim obtains the key when needed. |
| Create/suspend Workspace users or manage Google Group membership (trajectory Workspace only) | `GWS_WORK_ADMIN_OAUTH` | human | **Not through `gws`.** `gws` 0.22.5 has no Admin SDK Directory alias (only `admin-reports`), and its documented `<api>:<version>` form for unlisted APIs is broken in this build: `gws admin:directory_v1 …` returns `validationError: Unknown service 'admin'` before auth is attempted (verified 2026-09-12, no credential involved). Use the credential directly under its one-touch wrapper: `secrets GWS_WORK_ADMIN_OAUTH -- <script>` where the script exchanges the authorized-user JSON (`client_id`, `client_secret`, `refresh_token`) for an access token at `https://oauth2.googleapis.com/token` (`grant_type=refresh_token`) and calls `https://admin.googleapis.com/admin/directory/v1/…` with the bearer token. Never print the token or the JSON. Verified end to end (2026-09-13, 2sv-exempt cleanup): `GET /groups?customer=my_customer&maxResults=200` (find a group by email), `GET /groups/<groupKey>/members`, `GET /users/<email>?fields=primaryEmail,isEnrolledIn2Sv,isEnforcedIn2Sv,suspended,lastLoginTime`, the org-wide 2SV sweep `GET /users?customer=my_customer&query=isEnrolledIn2Sv%3Dfalse&maxResults=200`, and the group-membership REMOVE `DELETE /groups/<groupKey>/members/<memberEmail>` (returns 204, empty body). Gotcha: a members list read immediately after a DELETE can be stale for ~20 s — the 204 is the truth; re-read after a pause before concluding a removal failed. Also verified (2026-09-14, a Slack-seat rename): membership-ADD `POST /groups/<groupKey>/members` with body `{"email": "<user>", "role": "MEMBER"}` (200, response `status: ACTIVE`); password reset `PUT /users/<key>` with `{"password": "<value>", "changePasswordAtNextLogin": false}` (200; generate the value in the script and never print it); `POST /users/<key>/signOut` (204, empty body; invalidates every session). Gotcha: a user in a 2SV-enforced OU who is not enrolled cannot sign in at all (`Your sign-in settings don't meet your organization's 2-Step Verification policy`); the sanctioned grace path is temporary membership in the hand-staffed `2sv-exempt@` group — `isEnforcedIn2Sv` on the user flips to false after ~20 min of propagation (observed 23:32Z add → 23:53Z effective), and removing the membership re-enforces through propagation too, not immediately as this row once said: `isEnforcedIn2Sv` still read false about 30 s after the membership `DELETE` returned 204 and read true within about 4 minutes (2026-09-30: 204 at ~00:02:15Z, false at 00:02:46Z, true at 00:06:19Z; one later read, not a polling loop). The 204 is the truth about membership; re-read `isEnforcedIn2Sv` after a pause before concluding enforcement is back. Do a machine-mailbox sign-in only under that window, then remove the membership, rotate the password to an unrecorded value and `signOut`; neither the password reset nor `signOut` revokes an OAuth grant whose scopes include no Gmail scope (a third-party sign-in grant, Taiga's, scoped only `openid`/`userinfo.email`/`userinfo.profile`, survived a reset plus `signOut` on 2026-09-30, verified by a token refresh and an authenticated API call before and after). Create and suspend writes (`POST /users`, `PUT /users/<key>` with `{"suspended": true}`) remain unexercised — the first session to run them records the exact calls here. The bundle is consented by `admin-sami@` (Sami's admin identity) since 2026-09-17, 8 scopes including `admin.directory.userschema.readonly`; a bundle minted by `sami@` has no admin authority. |

### Select a profile

`GWS_ACCOUNT` chooses the profile and its credential store. It accepts only `personal`,
`theorem`, or `trajectory`; unset, empty, or any other value is an error. The stores are
separate, one per profile (see the table above).

An `authorized_user` credential in `secrets` contains the refresh token and the OAuth client config.
The shim materializes the credential privately and passes its `client_id`
and `client_secret` directly to the real CLI. Consuming a credential needs no
`~/.config/gws/<profile>/client_secret.json` file at all, so do not add a local client config to
troubleshoot a credential consumer. If a `403 Caller does not have required permission to use
project ...` names an unrelated project, Application Default Credentials leaked that machine's
quota project; the credential is not broken.

```bash
GWS_ACCOUNT=personal gws drive files list --params '{"pageSize": 10}'
GWS_ACCOUNT=theorem gws drive files list --params '{"pageSize": 10}'
GWS_ACCOUNT=trajectory gws drive files list --params '{"pageSize": 10}'
```

Reading never needs a `secrets` wrapper and never needs a YubiKey touch: the shim re-executes
under `secrets` to fetch the selected profile's agent-tier read credential, materializes it in a
private temporary file, and selects that profile's store before starting the real CLI.

### `gws auth` manages a store, not an operation credential

`gws auth …` receives no injected credential, because it creates and inspects credentials. It
still honours `GWS_ACCOUNT`: `GWS_ACCOUNT=personal gws auth status` uses the personal store. The
shim defaults `GOOGLE_WORKSPACE_CLI_CONFIG_DIR` to the selected profile's store but preserves a
value the caller already supplied, in which case `GWS_ACCOUNT` is not needed.

### Explicit CLI credential overrides

Preset `GOOGLE_WORKSPACE_CLI_TOKEN` (a raw access token) or `GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE` (an authorized-user JSON) and the shim execs the real CLI untouched. This takes priority over profile selection and all normal shim credential routing.

### Adding a profile

1. Add the name to `GWS_PROFILES` in `shims/gws` and to the profile loop in `check_gws` in
   `scripts/agent-access`.
2. Get the OAuth client from whoever owns that Google account's org (a Desktop-app client in a
   project that org owns), then run the consent with the client passed as environment variables;
   gws did not pick up a hand-built `client_secret.json`:

   ```bash
   GOOGLE_WORKSPACE_CLI_CLIENT_ID="$(jq -r .installed.client_id client.json)" \
   GOOGLE_WORKSPACE_CLI_CLIENT_SECRET="$(jq -r .installed.client_secret client.json)" \
   GWS_ACCOUNT=<profile> gws auth login --scopes 'openid,https://www.googleapis.com/auth/userinfo.email,https://www.googleapis.com/auth/drive,https://www.googleapis.com/auth/documents,https://www.googleapis.com/auth/spreadsheets,https://www.googleapis.com/auth/presentations,https://www.googleapis.com/auth/calendar,https://www.googleapis.com/auth/gmail.readonly'
   ```

   The consent page shows the OAuth client's app name, not the profile name: tell the human
   which account to pick in Google's account chooser before opening it.
3. In a git clone of the dotfiles, store the grant as `GWS_<PROFILE>_READ_OAUTH` in secretsd's
   dotfiles source, without the value touching argv, then commit and push `secrets.env`:

   ```bash
   GWS_ACCOUNT=<profile> gws auth export --unmasked | jq -c . | jq -R . \
     | sops set --value-stdin secrets.env '["GWS_<PROFILE>_READ_OAUTH"]'
   ```

4. Delete `~/.config/gws/<profile>/credentials.enc` and `client_secret.json`; the shim reads only
   the secretsd copy.

To undo a login into the wrong store, delete that store's files. Do not revoke the grant at
Google: Google may revoke every token issued under the same grant (RFC 7009 permits it), which
would take the stored credential for the same client and account with it.

## Gotchas

- **`fields` parameter required** for some methods (`about.get`, `comments.*`). Check `gws schema` if you get empty responses.
- **Never conclude a capability is missing because a name you guessed isn't there.** `gws` mirrors the Google APIs, so method and request names are Google's, not ones you'd invent — grepping for a plausible name and finding nothing tells you only that your guess was wrong. `gws schema <service.resource.method> --resolve-refs` prints every request type and field for that method; `gws --help` lists the services. Enumerate, then conclude.
- **Google Docs tabs are fully supported, under names you would not guess.** Create with a `docs.documents.batchUpdate` request keyed `addDocumentTab` (not `createTab`), whose `tabProperties` carry `title`, `iconEmoji`, and `parentTabId` for nesting; also `deleteTab` and `updateDocumentTabProperties`. Reading matters as much as writing: `docs.documents.get` omits tab content entirely unless you pass `includeTabsContent: true`, and requests target a specific tab via `TabsCriteria` / `tabId`.
- **Sheets ranges use `!`** which bash interprets as history expansion. Always single-quote: `'Sheet1!A1:C10'`
- **OAuth scope limits**: Unverified apps (testing mode) limited to ~25 scopes. Use `-s drive` not `-s all` for personal use.
- **Getting file content depends on how the file was stored.** Native Docs/Sheets aren't downloadable — use `files.export` with a target MIME type (`application/pdf`, `text/csv`), not `files.get`. Office files *uploaded* to Drive (`.docx`/`.xlsx`, shown as `rtpof=true` in the browser URL) are the opposite: `files.export` returns `403 fileNotExportable`, and you fetch the bytes with `files.get` + `alt: media`.
- **A `404 File not found` on a file you can open in the browser usually means you omitted `supportsAllDrives: true`**, not that you lack access. Shared-drive files are invisible without it. Add it to every Drive call — get, list, copy, delete.
- **Download/copy restrictions have one legitimate way through**: an uploaded Office file with `canDownload: false` / `canCopy: false` still yields to `files.copy` with `mimeType: application/vnd.google-apps.document`, because conversion produces a new file you own. Used for counterparty contracts we need to redline — see the `drive-locked-files` skill for when that's appropriate and where the copy may land.
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
