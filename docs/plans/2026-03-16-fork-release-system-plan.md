# Fork Release System Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Get jj fork releases working end-to-end: push to sami -> CI builds -> `mise install` downloads pre-built binary. Then extract shared actions for other forks.

**Architecture:** Phase 1 fixes the jj fork's existing sami-build.yml in-place (add targets, fix tag bug, make mise-compatible). Phase 2 extracts shared composite actions to sjawhar/.github. Phase 3 updates dotfiles.

**Tech Stack:** GitHub Actions, Rust/cargo, mise, softprops/action-gh-release

---

## Phase 1: Fix jj fork workflow and prove end-to-end

### Task 1: Fix sami-build.yml on the jj fork

The existing workflow at `sjawhar/jj:.github/workflows/sami-build.yml` (sami branch)
has issues:
- Each matrix job computes its own timestamp, so artifact names don't match the release tag
- Only 3 targets (needs 5: linux amd64/arm64, macOS amd64/arm64, Windows amd64)
- Marked as `prerelease: true` (mise `latest` skips pre-releases)
- Version grep reads root `Cargo.toml` but jj's version is in `cli/Cargo.toml`

**Files:**
- Modify: `sjawhar/jj:.github/workflows/sami-build.yml` (on sami branch)

**Step 1: Clone jj fork and check out sami branch**

The jj repo is already at `~/jj/default`. Work there.

```bash
cd ~/jj/default
jj git fetch
jj log -r 'sami' --limit 3
```

**Step 2: Edit the workflow**

Replace `.github/workflows/sami-build.yml` with the fixed version.

Key changes from the existing workflow:
1. Add a `version` job that computes the tag ONCE and passes it to other jobs
2. Expand matrix to 5 targets (add linux-aarch64, macos-x86_64)
3. Change `prerelease: false` in the release step
4. Read version from `cli/Cargo.toml` instead of root `Cargo.toml`
5. The release job uses the tag from the version job (not its own computation)

```yaml
name: Build sami branch

on:
  push:
    branches:
      - sami
  workflow_dispatch:

permissions:
  contents: write

env:
  CARGO_INCREMENTAL: 0

jobs:
  version:
    name: Compute version
    runs-on: ubuntu-latest
    outputs:
      tag: ${{ steps.version.outputs.tag }}
    steps:
      - uses: actions/checkout@v4
      - name: Compute version tag
        id: version
        run: |
          ver=$(grep '^version' cli/Cargo.toml | head -1 | sed 's/version = "\(.*\)"/\1/')
          timestamp=$(date -u +%Y%m%d-%H%M%S)
          tag="v${ver}-sami.${timestamp}"
          echo "tag=${tag}" >> "$GITHUB_OUTPUT"
          echo "Computed tag: ${tag}"

  build:
    name: Build ${{ matrix.build }}
    needs: version
    strategy:
      fail-fast: false
      matrix:
        build: [linux-x86_64, linux-aarch64, macos-x86_64, macos-aarch64, win-x86_64]
        include:
          - build: linux-x86_64
            os: ubuntu-24.04
            target: x86_64-unknown-linux-musl
          - build: linux-aarch64
            os: ubuntu-24.04
            target: aarch64-unknown-linux-musl
          - build: macos-x86_64
            os: macos-15
            target: x86_64-apple-darwin
          - build: macos-aarch64
            os: macos-15
            target: aarch64-apple-darwin
          - build: win-x86_64
            os: windows-2022
            target: x86_64-pc-windows-msvc
    runs-on: ${{ matrix.os }}
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v4

      - name: Install packages (Ubuntu)
        if: startsWith(matrix.os, 'ubuntu')
        run: |
          sudo apt-get update
          sudo apt-get install -y --no-install-recommends musl-tools
          if [[ "${{ matrix.target }}" == aarch64* ]]; then
            sudo apt-get install -y --no-install-recommends \
              gcc-aarch64-linux-gnu \
              libc6-dev-arm64-cross
          fi

      - name: Install Rust
        uses: dtolnay/rust-toolchain@stable
        with:
          target: ${{ matrix.target }}

      - name: Configure aarch64 cross-compilation
        if: contains(matrix.target, 'aarch64') && contains(matrix.target, 'linux')
        run: |
          echo '[target.aarch64-unknown-linux-musl]' >> ~/.cargo/config.toml
          echo 'linker = "aarch64-linux-gnu-gcc"' >> ~/.cargo/config.toml

      - name: Build release binary
        shell: bash
        run: cargo build --target ${{ matrix.target }} --release -p jj-cli

      - name: Package binary
        id: package
        shell: bash
        run: |
          tag="${{ needs.version.outputs.tag }}"
          outdir="target/${{ matrix.target }}/release"
          staging="jj-${tag}-${{ matrix.target }}"
          mkdir "$staging"

          if [[ "${{ matrix.os }}" == windows* ]]; then
            cp "$outdir/jj.exe" "$staging/"
            cd "$staging"
            7z a "../$staging.zip" .
            echo "asset=${staging}.zip" >> "$GITHUB_OUTPUT"
          else
            cp "$outdir/jj" "$staging/"
            tar czf "$staging.tar.gz" -C "$staging" .
            echo "asset=${staging}.tar.gz" >> "$GITHUB_OUTPUT"
          fi

      - name: Upload artifact
        uses: actions/upload-artifact@v4
        with:
          name: jj-${{ matrix.target }}
          path: ${{ steps.package.outputs.asset }}

  release:
    name: Create release
    needs: [version, build]
    runs-on: ubuntu-latest
    steps:
      - name: Download all artifacts
        uses: actions/download-artifact@v4
        with:
          path: artifacts

      - name: Create release
        uses: softprops/action-gh-release@v2
        with:
          tag_name: ${{ needs.version.outputs.tag }}
          name: "sami build ${{ needs.version.outputs.tag }}"
          body: |
            Automated build from the `sami` branch.

            Commit: ${{ github.sha }}
          files: artifacts/**/*
          prerelease: false
```

**Step 3: Commit and push**

```bash
jj describe -m "ci: fix sami-build — consistent tags, 5 targets, non-prerelease"
jj bookmark set sami
jj git push --bookmark sami
```

**Step 4: Watch the workflow run**

```bash
# Wait a moment for GitHub to pick up the push, then:
gh run list --repo sjawhar/jj --branch sami --limit 3
# Once a run appears:
gh run watch --repo sjawhar/jj <run-id>
```

Expected: 5 build jobs + 1 version job + 1 release job, all green.

**Step 5: Verify the release**

```bash
gh release view --repo sjawhar/jj --json tagName,name,assets \
  --jq '{tag: .tagName, name: .name, assets: [.assets[].name]}'
```

Expected: a release with 5 assets (4 tar.gz + 1 zip), all sharing the same tag,
marked as a full release (not pre-release).

### Task 2: Verify mise install from the release

**Step 1: Test mise github backend with the new release**

```bash
# First, check what mise sees
mise ls-remote github:sjawhar/jj 2>&1 | head -10

# Try installing
mise use github:sjawhar/jj@latest

# Verify the binary works
mise exec github:sjawhar/jj -- jj version
```

If mise can't auto-detect the binary, try with asset_pattern:
```bash
mise use "github:sjawhar/jj[asset_pattern=jj-*-x86_64-unknown-linux-musl*]@latest"
```

**Step 2: Document what worked**

Note the exact mise.toml syntax that works. We'll use this in Task 4.

### Task 3: Verify the binary actually works

```bash
# Create a temp directory and test basic jj operations
cd $(mktemp -d)
jj init
jj status
echo "test" > test.txt
jj log
jj describe -m "test commit"
jj log
```

Expected: all commands work, jj binary is functional.

---

## Phase 2: Extract composite actions to sjawhar/.github

Only start this after Phase 1 is fully working.

### Task 4: Create the sjawhar/.github repo

**Step 1: Create the repo**

```bash
gh repo create sjawhar/.github --public --description "Shared GitHub Actions for sjawhar repos"
```

**Step 2: Create sami-version composite action**

Create `.github/actions/sami-version/action.yml`:

```yaml
name: Sami Version
description: Compute a sami version tag from project metadata

inputs:
  source:
    description: 'Version source: cargo, bun, or node'
    required: true
  manifest_path:
    description: 'Path to version file (default: Cargo.toml or package.json)'
    required: false
    default: ''

outputs:
  version:
    description: 'Raw version (e.g., 0.39.0)'
    value: ${{ steps.compute.outputs.version }}
  tag:
    description: 'Full sami tag (e.g., v0.39.0-sami.20260314-120000)'
    value: ${{ steps.compute.outputs.tag }}
  timestamp:
    description: 'Timestamp portion (YYYYMMDD-HHMMSS)'
    value: ${{ steps.compute.outputs.timestamp }}

runs:
  using: composite
  steps:
    - name: Compute version
      id: compute
      shell: bash
      run: |
        SOURCE="${{ inputs.source }}"
        MANIFEST="${{ inputs.manifest_path }}"

        case "$SOURCE" in
          cargo)
            FILE="${MANIFEST:-Cargo.toml}"
            VERSION=$(grep '^version' "$FILE" | head -1 | sed 's/version = "\(.*\)"/\1/')
            ;;
          bun|node)
            FILE="${MANIFEST:-package.json}"
            VERSION=$(jq -r '.version' "$FILE")
            ;;
          *)
            echo "::error::Unknown source: $SOURCE"
            exit 1
            ;;
        esac

        TIMESTAMP=$(date -u +%Y%m%d-%H%M%S)
        TAG="v${VERSION}-sami.${TIMESTAMP}"

        echo "version=${VERSION}" >> "$GITHUB_OUTPUT"
        echo "tag=${TAG}" >> "$GITHUB_OUTPUT"
        echo "timestamp=${TIMESTAMP}" >> "$GITHUB_OUTPUT"
        echo "Computed: version=${VERSION} tag=${TAG}"
```

**Step 3: Create sami-release composite action**

Create `.github/actions/sami-release/action.yml`:

```yaml
name: Sami Release
description: Create a GitHub Release and upload assets

inputs:
  tag:
    description: 'Release tag (from sami-version)'
    required: true
  files:
    description: 'Glob pattern for files to upload (empty string for tag-only release)'
    required: false
    default: ''
  body:
    description: 'Release body text'
    required: false
    default: |
      Automated build from the `sami` branch.
      Commit: ${{ github.sha }}
  prerelease:
    description: 'Mark as pre-release'
    required: false
    default: 'false'

runs:
  using: composite
  steps:
    - name: Create release
      uses: softprops/action-gh-release@v2
      with:
        tag_name: ${{ inputs.tag }}
        name: "sami build ${{ inputs.tag }}"
        body: ${{ inputs.body }}
        files: ${{ inputs.files }}
        prerelease: ${{ inputs.prerelease }}
```

**Step 4: Commit, tag, and push**

```bash
cd <.github repo>
git add -A
git commit -m "feat: add sami-version and sami-release composite actions"
git tag v1
git push origin main --tags
```

### Task 5: Update jj workflow to use composite actions

**Step 1: Edit sami-build.yml to use the shared actions**

Replace the inline version computation with:
```yaml
  version:
    name: Compute version
    runs-on: ubuntu-latest
    outputs:
      tag: ${{ steps.version.outputs.tag }}
    steps:
      - uses: actions/checkout@v4
      - uses: sjawhar/.github/.github/actions/sami-version@v1
        id: version
        with:
          source: cargo
          manifest_path: cli/Cargo.toml
```

Replace the inline release creation with:
```yaml
      - uses: sjawhar/.github/.github/actions/sami-release@v1
        with:
          tag: ${{ needs.version.outputs.tag }}
          files: artifacts/**/*
```

**Step 2: Push and verify**

```bash
jj describe -m "ci: use shared composite actions from sjawhar/.github"
jj bookmark set sami
jj git push --bookmark sami
```

Watch the run, verify the release is created identically to before.

---

## Phase 3: Update dotfiles

### Task 6: Update mise.toml

**Step 1: Edit mise.toml**

Replace the cargo source build line with the github backend line (using whatever
syntax was proven in Task 2).

**Step 2: Test**

```bash
mise install
jj version
jj status  # run from any jj repo to verify it works
```

### Task 7: Clean up sync-oss

**Step 1: Delete files**

- `sync-repos.json`
- `scripts/sync-oss`
- `plugins/sjawhar/skills/sync-oss/SKILL.md`

**Step 2: Commit**

```bash
jj describe -m "chore: remove sync-oss — replaced by fork release system"
jj new
```

---

## Phase 4: Add opencode and oh-my-opencode (future)

Not in this plan. Start a new planning session once Phase 1-3 are proven.
Requires:
- opencode: sami-build.yml + GitHub Pages deploy for frontend + sed patching
- oh-my-opencode: sami-build.yml + npm OIDC setup + scoped package publish
