# Centralized Fork Release System

## Problem

Maintaining custom forks of 3 projects (jj, opencode, oh-my-opencode) requires building
from source on every machine. This is slow, error-prone, and makes distribution to
coworkers impractical. We need a system where pushing to the `sami` branch triggers a
CI build that produces installable artifacts.

## Forks in Scope

| Fork | Language | Repo | Key patches |
|------|----------|------|-------------|
| jj | Rust | sjawhar/jj | workspace-cli, LFS ignore-filters, gitattr filter fixes |
| opencode | TypeScript/Bun | sjawhar/opencode | voice mode (STT/TTS), OPENCODE_WEB_URL, session fixes, provider tweaks |
| oh-my-opencode | TypeScript/Bun | sjawhar/oh-my-opencode | TBD (not yet published) |

## Architecture

### Central Repo: `sjawhar/.github`

Contains shared composite actions used by all fork workflows. This is a public GitHub
repo that any fork can reference.

**Composite actions:**

#### `sami-version`

Computes a deterministic version tag from the project's version metadata.

- **Inputs:**
  - `source`: `cargo` | `bun` | `node` — which file to read the version from
  - `manifest_path`: optional path to version file (default: root `Cargo.toml` or `package.json`)
  - `version_override`: optional explicit version string
- **Logic:**
  - `cargo`: reads `version` from the specified `Cargo.toml`
  - `bun`/`node`: reads `version` from the specified `package.json`
  - Appends `-sami.YYYYMMDD-HHMMSS` timestamp (UTC)
- **Outputs:**
  - `version`: raw version (e.g., `0.39.0`)
  - `tag`: full tag (e.g., `v0.39.0-sami.20260314-120000`)
  - `timestamp`: the YYYYMMDD-HHMMSS portion

#### `sami-release`

Creates a GitHub Release and uploads assets.

- **Inputs:**
  - `tag`: the version tag from `sami-version`
  - `files`: glob pattern for files to upload
  - `prerelease`: boolean, default `false` (NOT pre-release, so mise `latest` works)
  - `body`: optional release body text
- **Implementation:** wraps `softprops/action-gh-release@v2`
- **Behavior:** multiple matrix jobs can upload to the same release concurrently

### Per-Fork Workflows

Each fork has a `sami-build.yml` on its `sami` branch. The workflow is 40-80 lines
and follows this structure:

1. Trigger on push to `sami` + `workflow_dispatch`
2. Call `sami-version` to compute the tag
3. Project-specific build steps (different per language)
4. Call `sami-release` to publish

## Per-Fork Details

### jj (Rust)

**Workflow: `sjawhar/jj/.github/workflows/sami-build.yml`**

```yaml
name: Sami Build
on:
  push:
    branches: [sami]
  workflow_dispatch:

jobs:
  build:
    strategy:
      fail-fast: false
      matrix:
        include:
          - os: ubuntu-latest
            target: x86_64-unknown-linux-musl
            binary: jj
          - os: ubuntu-latest
            target: aarch64-unknown-linux-musl
            binary: jj
          - os: macos-latest
            target: x86_64-apple-darwin
            binary: jj
          - os: macos-latest
            target: aarch64-apple-darwin
            binary: jj
          - os: windows-latest
            target: x86_64-pc-windows-msvc
            binary: jj.exe
    runs-on: ${{ matrix.os }}
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v4
      - uses: sjawhar/.github/.github/actions/sami-version@v1
        id: version
        with:
          source: cargo
          manifest_path: cli/Cargo.toml
      - uses: dtolnay/rust-toolchain@stable
        with:
          targets: ${{ matrix.target }}
      - name: Install musl tools
        if: contains(matrix.target, 'musl')
        run: sudo apt-get install -y musl-tools
      - name: Install cross-compilation tools (aarch64)
        if: contains(matrix.target, 'aarch64') && contains(matrix.target, 'linux')
        run: sudo apt-get install -y gcc-aarch64-linux-gnu
      - name: Build
        run: cargo build --release --target ${{ matrix.target }} -p jj-cli
      - name: Package
        shell: bash
        run: |
          ARCHIVE="jj-${{ steps.version.outputs.tag }}-${{ matrix.target }}"
          if [[ "${{ matrix.os }}" == "windows-latest" ]]; then
            7z a "${ARCHIVE}.zip" "target/${{ matrix.target }}/release/${{ matrix.binary }}"
          else
            tar czf "${ARCHIVE}.tar.gz" -C "target/${{ matrix.target }}/release" "${{ matrix.binary }}"
          fi
      - uses: sjawhar/.github/.github/actions/sami-release@v1
        with:
          tag: ${{ steps.version.outputs.tag }}
          files: jj-*.*
```

**Artifact naming:** `jj-v0.39.0-sami.20260314-120000-x86_64-unknown-linux-musl.tar.gz`

**Consumer install:**
```toml
# mise.toml
"github:sjawhar/jj" = "latest"
```

### opencode (TypeScript/Bun)

**Three jobs in `sjawhar/opencode/.github/workflows/sami-build.yml`:**

1. **version** — computes the sami version tag
2. **build-frontend** — builds the SolidJS frontend and deploys to GitHub Pages
3. **build-cli** — builds the CLI binary with the frontend URL baked in

**Frontend hosting:**
- Deployed to GitHub Pages at `sjawhar.github.io/opencode/{tag}/`
- Each release gets its own directory, so older versions keep working
- Retention policy: keep last 5 versions, delete older directories during deploy
- At 62MB per build, 5 versions = ~310MB (well within GitHub Pages 1GB limit)

**Build-time URL patching (sed replacement):**
The opencode source has:
```typescript
// packages/opencode/src/server/server.ts
const appHost = appDir || "https://app.opencode.ai"
```

The build workflow patches this with sed before building:
```bash
FRONTEND_URL="https://sjawhar.github.io/opencode/${TAG}/"
sed -i "s|https://app.opencode.ai|${FRONTEND_URL}|g" packages/opencode/src/server/server.ts
```

At runtime, `OPENCODE_WEB_URL` env var still overrides everything (existing behavior).
The sed target (`https://app.opencode.ai`) is a stable string unlikely to change.

**Workflow sketch:**
```yaml
name: Sami Build
on:
  push:
    branches: [sami]
  workflow_dispatch:

jobs:
  version:
    runs-on: ubuntu-latest
    outputs:
      tag: ${{ steps.version.outputs.tag }}
    steps:
      - uses: actions/checkout@v4
      - uses: sjawhar/.github/.github/actions/sami-version@v1
        id: version
        with:
          source: bun

  build-frontend:
    needs: version
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: oven-sh/setup-bun@v2
      - run: bun install
      - run: bun turbo build --filter=app
        working-directory: packages/app
      - name: Deploy to GitHub Pages
        # Deploy to /{tag}/ subdirectory
        # Uses peaceiris/actions-gh-pages or similar
      - name: Cleanup old versions
        # List version directories, sort by date, delete all but last 5

  build-cli:
    needs: [version, build-frontend]
    strategy:
      fail-fast: false
      matrix:
        include:
          - os: ubuntu-latest
            target: linux-x64
          - os: ubuntu-latest
            target: linux-arm64
          - os: macos-latest
            target: darwin-x64
          - os: macos-latest
            target: darwin-arm64
          - os: windows-latest
            target: win-x64
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: oven-sh/setup-bun@v2
      - run: bun install
      - name: Patch frontend URL
        run: |
          FRONTEND_URL="https://sjawhar.github.io/opencode/${{ needs.version.outputs.tag }}/"
          sed -i "s|https://app.opencode.ai|${FRONTEND_URL}|g" packages/opencode/src/server/server.ts
      - name: Build CLI
        run: |
          # Uses the existing build script or bun build --compile
      - name: Package
        # tar.gz / zip per platform
      - uses: sjawhar/.github/.github/actions/sami-release@v1
        with:
          tag: ${{ needs.version.outputs.tag }}
          files: opencode-*.*
```

**Consumer install:**
```toml
# mise.toml
"github:sjawhar/opencode" = "latest"
```

The CLI binary defaults to loading frontend from the GitHub Pages URL for its version.
Users can override with `OPENCODE_WEB_URL` for local dev or a different host.

### oh-my-opencode (TypeScript/Bun)

**Distribution via npm** under `@sjawhar/oh-my-opencode`.

**Workflow sketch:**
```yaml
name: Sami Build
on:
  push:
    branches: [sami]
  workflow_dispatch:

jobs:
  version:
    runs-on: ubuntu-latest
    outputs:
      tag: ${{ steps.version.outputs.tag }}
      version: ${{ steps.version.outputs.version }}
    steps:
      - uses: actions/checkout@v4
      - uses: sjawhar/.github/.github/actions/sami-version@v1
        id: version
        with:
          source: bun

  publish:
    needs: version
    runs-on: ubuntu-latest
    permissions:
      contents: write
      id-token: write  # for npm OIDC Trusted Publishing (provenance attestation)
    steps:
      - uses: actions/checkout@v4
      - uses: oven-sh/setup-bun@v2
      - uses: actions/setup-node@v4
        with:
          registry-url: https://registry.npmjs.org
      - run: bun install
      - name: Patch version and name
        run: |
          # Update package.json: name -> @sjawhar/oh-my-opencode
          # Update version -> sami version
          jq '.name = "@sjawhar/oh-my-opencode" | .version = "${{ needs.version.outputs.tag }}"' \
            package.json > package.json.tmp && mv package.json.tmp package.json
      - run: bun run build
      - run: npm publish --provenance --access public
        env:
          NODE_AUTH_TOKEN: ${{ secrets.NPM_TOKEN }}
      - uses: sjawhar/.github/.github/actions/sami-release@v1
        with:
          tag: ${{ needs.version.outputs.tag }}
          files: ""  # no binary assets, just the tag
          body: "Published to npm as @sjawhar/oh-my-opencode"
```

**Consumer install:**
```bash
bunx @sjawhar/oh-my-opencode
```

## Changes to dotfiles

### mise.toml

```toml
# REMOVE: building jj from source
# "cargo:https://github.com/jj-vcs/jj" = { version = "branch:workspace-cli", crate = "jj-cli", bin = "jj" }

# ADD: install from fork releases
"github:sjawhar/jj" = "latest"
# "github:sjawhar/opencode" = "latest"  # once opencode fork releases exist

# KEEP: Rust toolchain for local development
rust = "1.92.0"
```

### Delete

- `sync-repos.json`
- `scripts/sync-oss`
- `plugins/sjawhar/skills/sync-oss/SKILL.md`

### .bashrc

Remove or update any `OPENCODE_WEB_URL` references once the CLI binary has the
default baked in.

## mise `latest` and Version Resolution

GitHub's API defines "latest release" as the most recent non-draft, non-prerelease
release. The sami releases MUST NOT be marked as pre-releases, otherwise
`mise use github:sjawhar/jj` with `latest` won't find them.

GitHub Releases are NOT inherited by forks (only git tags are). So `sjawhar/jj`
only has the sami releases, not upstream's v0.38.0, v0.39.0, etc. This means
`latest` resolves correctly without any filtering.

During implementation, verify that mise correctly auto-detects the binary name
from the tarball (e.g., that `jj-v0.39.0-sami.20260314-x86_64-unknown-linux-musl.tar.gz`
extracts to a binary named `jj`). If asset naming causes issues, use `asset_pattern`:
```toml
"github:sjawhar/jj" = { version = "latest", asset_pattern = "jj-*-x86_64-unknown-linux-musl*" }
```

## Resolved Decisions

1. **sami-version genericity**: The action accepts a `manifest_path` input.
   jj passes `manifest_path: cli/Cargo.toml`; others use the default root path.

2. **GitHub Pages retention**: Keep the last 5 frontend versions deployed.
   The deploy job deletes older version directories. At 62MB per build, 5 versions
   = ~310MB, well within the 1GB GitHub Pages limit.

3. **npm auth**: Use OIDC Trusted Publishing for oh-my-opencode npm publishing.
   One-time setup: configure Trusted Publishing in npm account settings, linking
   the `sjawhar/oh-my-opencode` repo and workflow to the `@sjawhar/oh-my-opencode`
   npm package.

4. **mise version filtering**: Not needed. GitHub Releases are per-repo (not inherited
   by forks), so `sjawhar/jj` only has sami releases. `latest` works correctly.
   Verify during implementation that mise auto-detects the binary name from tarballs.

5. **opencode build-time patching**: Use `sed` replacement before build.
   `sed -i 's|https://app.opencode.ai|${FRONTEND_URL}|g' packages/opencode/src/server/server.ts`
   Zero source changes needed. `OPENCODE_WEB_URL` env var still overrides at runtime.

6. **Platform matrix**: 5 targets for binary projects (linux amd64/arm64, macOS amd64/arm64,
   Windows amd64). oh-my-opencode uses bun's platform targets instead.
