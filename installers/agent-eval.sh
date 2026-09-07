#!/bin/bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

AGENT_EVAL_VERSION="$({ mise env --cd "$DOTFILES_DIR" --json; } | python3 -c 'import json, sys; print(json.load(sys.stdin)["AGENT_EVAL_VERSION"])')"
readonly AGENT_EVAL_VERSION
readonly AGENT_EVAL_REPOSITORY="sjawhar/agent-eval-data"
readonly AGENT_EVAL_TAG="v${AGENT_EVAL_VERSION}"
readonly AGENT_EVAL_WHEEL="agent_evals-${AGENT_EVAL_VERSION}-py3-none-any.whl"
readonly AGENT_EVAL_CACHE_ROOT="${XDG_CACHE_HOME:-${HOME}/.cache}/agent-eval/releases/${AGENT_EVAL_TAG}"
readonly AGENT_EVAL_SUMS_PATH="${AGENT_EVAL_CACHE_ROOT}/SHA256SUMS"
readonly AGENT_EVAL_WHEEL_CACHE_ROOT="${AGENT_EVAL_CACHE_ROOT}/sha256"

release_wheel_sha() {
    local sums_path="$1"
    [[ -f "$sums_path" ]] || return 1
    python3 - "$sums_path" "$AGENT_EVAL_WHEEL" <<'PY'
import re
import sys
from pathlib import Path

sums_path, wheel = sys.argv[1:]
matches = []
for line in Path(sums_path).read_text().splitlines():
    match = re.fullmatch(r"([0-9a-f]{64}) [ *](.+)", line)
    if match and match.group(2) == wheel:
        matches.append(match.group(1))
if len(matches) != 1:
    raise SystemExit(1)
print(matches[0])
PY
}

verified_cached_wheel() {
    local expected_sha wheel_path actual_sha
    expected_sha="$(release_wheel_sha "$AGENT_EVAL_SUMS_PATH")" || return 1
    wheel_path="${AGENT_EVAL_WHEEL_CACHE_ROOT}/${expected_sha}/${AGENT_EVAL_WHEEL}"
    [[ -f "$wheel_path" ]] || return 1
    actual_sha="$(sha256sum "$wheel_path" | cut -d' ' -f1)"
    [[ "$actual_sha" == "$expected_sha" ]] || return 1
    printf '%s\n' "$wheel_path"
}

download_release_wheel() {
    local download_dir downloaded_sums expected_sha downloaded_wheel wheel_path actual_sha cached_sha
    if verified_cached_wheel; then
        return
    fi

    download_dir="$(mktemp -d)"
    gh release download "$AGENT_EVAL_TAG" \
        --repo "$AGENT_EVAL_REPOSITORY" \
        --pattern "$AGENT_EVAL_WHEEL" \
        --pattern SHA256SUMS \
        --dir "$download_dir" >&2
    downloaded_sums="${download_dir}/SHA256SUMS"
    downloaded_wheel="${download_dir}/${AGENT_EVAL_WHEEL}"
    expected_sha="$(release_wheel_sha "$downloaded_sums")" || {
        echo "agent-evals: release checksum manifest has no unique wheel entry" >&2
        exit 1
    }
    actual_sha="$(sha256sum "$downloaded_wheel" | cut -d' ' -f1)"
    [[ "$actual_sha" == "$expected_sha" ]] || {
        echo "agent-evals: release wheel checksum verification failed" >&2
        exit 1
    }

    wheel_path="${AGENT_EVAL_WHEEL_CACHE_ROOT}/${expected_sha}/${AGENT_EVAL_WHEEL}"
    mkdir -p "$(dirname "$wheel_path")"
    if [[ -e "$wheel_path" ]]; then
        cached_sha="$(sha256sum "$wheel_path" | cut -d' ' -f1)"
        [[ "$cached_sha" == "$expected_sha" ]] || {
            echo "agent-evals: cached wheel conflicts with its content-addressed origin" >&2
            exit 1
        }
        rm -f "$downloaded_wheel"
    else
        mv "$downloaded_wheel" "$wheel_path"
    fi
    if [[ -e "$AGENT_EVAL_SUMS_PATH" ]]; then
        cached_sha="$(release_wheel_sha "$AGENT_EVAL_SUMS_PATH")" || {
            echo "agent-evals: cached checksum manifest is invalid" >&2
            exit 1
        }
        [[ "$cached_sha" == "$expected_sha" ]] || {
            echo "agent-evals: cached checksum manifest conflicts with the release" >&2
            exit 1
        }
        rm -f "$downloaded_sums"
    else
        mkdir -p "$AGENT_EVAL_CACHE_ROOT"
        mv "$downloaded_sums" "$AGENT_EVAL_SUMS_PATH"
    fi
    rmdir "$download_dir"
    verified_cached_wheel
}

installed_tool_matches_release_wheel() {
    local tool_dir receipt wheel_path wheel_url expected_sha
    wheel_path="$(verified_cached_wheel)" || return 1
    expected_sha="$(basename "$(dirname "$wheel_path")")"
    wheel_url="$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve().as_uri())' "$wheel_path")"
    tool_dir="$(uv tool dir)"
    receipt="${tool_dir}/agent-evals/uv-receipt.toml"
    [[ -f "$receipt" ]] || return 1
    python3 - "$receipt" "$wheel_path" <<'PY' || return 1
import sys
import tomllib
from pathlib import Path

receipt_path, expected_path = sys.argv[1:]
receipt_data = tomllib.loads(Path(receipt_path).read_text())
requirements = receipt_data.get("tool", {}).get("requirements")
matches = [
    requirement
    for requirement in requirements if isinstance(requirement, dict)
    and requirement.get("name") == "agent-evals"
]
if len(matches) != 1 or matches[0].get("path") != expected_path:
    raise SystemExit(1)
PY
    "${tool_dir}/agent-evals/bin/python" - "$AGENT_EVAL_VERSION" "$wheel_url" "$expected_sha" <<'PY'
import json
import sys
from importlib.metadata import distribution
from pathlib import Path

version, expected_url, expected_sha = sys.argv[1:]
dist = distribution("agent-evals")
if dist.version != version:
    raise SystemExit(1)
if f"/sha256/{expected_sha}/" not in expected_url:
    raise SystemExit(1)
direct_url = Path(dist._path) / "direct_url.json"
if not direct_url.is_file():
    raise SystemExit(1)
metadata = json.loads(direct_url.read_text())
if metadata.get("url") != expected_url:
    raise SystemExit(1)
if not isinstance(metadata.get("archive_info"), dict):
    raise SystemExit(1)
if "dir_info" in metadata or "vcs_info" in metadata:
    raise SystemExit(1)
PY
}

AGENT_EVAL_WHEEL_PATH="$(download_release_wheel)"
if installed_tool_matches_release_wheel; then
    echo "agent-evals ${AGENT_EVAL_VERSION} already installed from the verified release wheel"
    return 0
fi

uv tool install --force "$AGENT_EVAL_WHEEL_PATH"
installed_tool_matches_release_wheel || {
    echo "agent-evals: uv did not retain verified direct-wheel provenance" >&2
    exit 1
}
