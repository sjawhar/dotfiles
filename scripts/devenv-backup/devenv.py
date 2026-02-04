#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["aioboto3", "aiofiles", "pydantic>=2.0", "tenacity"]
# ///
"""
DevEnv Capture/Restore System

Capture and restore development environment state (jj repositories, workspaces, files,
and Claude Code session data).

Usage:
    # Generate manifest (files included by default)
    uv run ~/.dotfiles/scripts/devenv-backup/devenv.py manifest
    uv run ~/.dotfiles/scripts/devenv-backup/devenv.py manifest --no-include-files

    # Backup to S3
    # Path structure: {base}/{machine}/{name}/ for backup, {base}/claude-code/{machine}/ for Claude, {base}/opencode/{machine}/ for OpenCode
    uv run ~/.dotfiles/scripts/devenv-backup/devenv.py backup --base s3://bucket/users/sami@metr.org/
    uv run ~/.dotfiles/scripts/devenv-backup/devenv.py backup --base s3://bucket/users/sami@metr.org/ --name 2026-01-20
    uv run ~/.dotfiles/scripts/devenv-backup/devenv.py backup --base s3://bucket/users/sami@metr.org/ --machine devpod --dry-run
    uv run ~/.dotfiles/scripts/devenv-backup/devenv.py backup --base s3://bucket/users/sami@metr.org/ --agent-instructions "Run install.sh"

    # List available backups for a machine
    uv run ~/.dotfiles/scripts/devenv-backup/devenv.py list-backups --base s3://bucket/users/sami@metr.org/
    uv run ~/.dotfiles/scripts/devenv-backup/devenv.py list-backups --base s3://bucket/users/sami@metr.org/ --machine devpod

    # Restore from backup
    uv run ~/.dotfiles/scripts/devenv-backup/devenv.py restore --base s3://bucket/users/sami@metr.org/  # lists available
    uv run ~/.dotfiles/scripts/devenv-backup/devenv.py restore --base s3://bucket/users/sami@metr.org/ --name 2026-01-20
    uv run ~/.dotfiles/scripts/devenv-backup/devenv.py restore --base s3://bucket/users/sami@metr.org/ --name 2026-01-20 --dry-run
    uv run ~/.dotfiles/scripts/devenv-backup/devenv.py restore --base s3://bucket/users/sami@metr.org/ --name 2026-01-20 --sessions-after 2026-01-15
"""

import argparse
import asyncio
import json
import os
import re
import shutil
import sys
from collections.abc import Coroutine
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Callable
from urllib.parse import urlparse

import aiofiles
from pydantic import BaseModel, field_validator

if TYPE_CHECKING:
    from types_aiobotocore_s3 import S3Client
    from types_aiobotocore_s3.type_defs import ObjectTypeDef

import aioboto3
from botocore.exceptions import ClientError
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

# ============================================================================
# Pydantic Models for Manifest
# ============================================================================


class WorkspaceData(BaseModel):
    """Workspace state data in manifest."""

    path: str
    current_change_id: str
    current_commit_id: str
    current_bookmark: str | None = None


class RepoData(BaseModel):
    """Repository data with remotes and workspaces."""

    remotes: dict[str, str]
    workspaces: dict[str, WorkspaceData] = {}

    def list_workspaces(self) -> list[tuple[str, WorkspaceData]]:
        """List all workspaces as (name, data) tuples."""
        return list(self.workspaces.items())


class UncommittedChange(BaseModel):
    """Uncommitted change entry in manifest."""

    change_id: str
    commit_id: str
    description: str
    bookmark: str | None = None
    workspace: str


class FileEntry(BaseModel):
    """File entry in manifest."""

    relative_path: str
    size_bytes: int
    mtime: str  # ISO format


class SymlinkEntry(BaseModel):
    """Symlink to be restored."""

    relative_path: str  # Path of the symlink itself
    target: str  # What it points to (relative to root_dir)


class Manifest(BaseModel):
    """Pydantic model for manifest validation and serialization."""

    version: int = 2
    captured_at: str
    hostname: str
    root_dir: str
    workspaces: dict[str, RepoData]
    uncommitted: list[UncommittedChange]
    agent_instructions: str | None = None
    files: list[FileEntry] | None = None
    symlinks: list[SymlinkEntry] | None = None

    @field_validator("root_dir")
    @classmethod
    def validate_root_dir(cls, v: str) -> str:
        if not v:
            raise ValueError("root_dir must be a non-empty string")
        return v


SKIP_DIRS = frozenset(
    [
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".cache",
        "target",
        "dist",
        ".cargo",
        ".rustup",
        ".mise",
        ".local",
        ".npm",
        ".bun",
        "go",
        ".gradle",
        ".m2",
    ]
)

# Dot-directories that should be searched for jj repos
ALLOWED_DOT_DIRS = frozenset([".dotfiles"])

# Claude Code files/dirs to sync (relative to claude dir)
# NOTE: .credentials.json is intentionally EXCLUDED (sensitive)
CLAUDE_SYNC_PATHS = frozenset(
    [
        ".claude.json",
        ".claude.json.backup",
        "history.jsonl",
        "projects",
        "plans",
        "todos",
        "file-history",
        "plugins/installed_plugins.json",
        "plugins/known_marketplaces.json",
    ]
)

# OpenCode storage directory (session data, messages, parts)
OPENCODE_STORAGE_DIR = Path.home() / ".local" / "share" / "opencode" / "storage"

# Subdirectories within opencode storage to sync
OPENCODE_SYNC_DIRS = frozenset(
    [
        "session",
        "message",
        "part",
        "project",
        "todo",
    ]
)


def should_skip_dir(name: str) -> bool:
    """Check if a directory should be skipped during traversal."""
    if name in ALLOWED_DOT_DIRS:
        return False
    return name in SKIP_DIRS or name.startswith(".")


def get_machine_name() -> str:
    """Get machine identifier from hostname.

    Sanitizes the hostname to only contain alphanumeric characters, hyphens,
    and underscores. Any invalid character sequences are replaced with a hyphen.
    """
    hostname = os.uname().nodename
    # Replace any sequence of non-alphanumeric/hyphen/underscore chars with a single hyphen
    sanitized = re.sub(r"[^a-zA-Z0-9_-]+", "-", hostname)
    # Strip leading/trailing hyphens
    sanitized = sanitized.strip("-")
    return sanitized or "unknown"


# Pattern for valid machine names and backup names: alphanumeric, hyphens, underscores
SAFE_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


def validate_safe_name(name: str, field: str) -> str:
    """Validate a name is non-empty and contains only safe characters.

    Args:
        name: The name to validate
        field: Field name for error messages (e.g., "Machine name", "Backup name")

    Raises:
        ValueError: If name is empty or contains invalid characters
    """
    if not name or not name.strip():
        raise ValueError(f"{field} cannot be empty")
    if not SAFE_NAME_PATTERN.match(name):
        raise ValueError(
            f"{field} must contain only alphanumeric characters, hyphens, and underscores: {name}"
        )
    return name


ALLOWED_URL_SCHEMES = ("https://", "http://", "git@", "ssh://", "git://")
DEFAULT_TIMEOUT = 30.0
CLONE_TIMEOUT = 300.0
KILL_WAIT_TIMEOUT = 5.0
MAX_CONCURRENT_CLONES = 4
CLI_DEFAULT_TIMEOUT = 120.0  # 2 minutes default for overall operation

# File backup constants
FILE_MAX_SIZE = 10 * 1024 * 1024  # 10MB
BINARY_CHECK_SIZE = 8192  # Check first 8KB for null bytes
MAX_CONCURRENT_S3_OPS = 20


# S3Object type alias - uses boto's ObjectTypeDef at type-check time
if TYPE_CHECKING:
    S3Object = ObjectTypeDef
else:
    S3Object = dict


class DevEnvError(Exception):
    """Base exception for devenv errors."""


class ManifestError(DevEnvError):
    """Error reading or parsing manifest."""


class ValidationError(DevEnvError):
    """Error validating manifest content."""


class JJOutputError(DevEnvError):
    """Error parsing jj command output."""


class RestoreError(DevEnvError):
    """Error during restore operation."""


def extract_bookmark_name(bookmark_line: str) -> str | None:
    """Extract bookmark name from jj bookmark list output line.

    Format: "bookmark_name: change_id commit_id description"
    Or: "bookmark_name@origin: change_id commit_id description"

    Returns the local bookmark name without remote suffix.
    """
    if not bookmark_line.strip():
        return None
    # Use partition for clarity - get everything before first ":", then before first "@"
    name_part = bookmark_line.partition(":")[0]
    name = name_part.partition("@")[0].strip()
    return name if name else None


def extract_bookmark_change_id(bookmark_line: str) -> str | None:
    """Extract change_id from jj bookmark list output line.

    Format: "bookmark_name: change_id commit_id description"

    Returns the change_id (first field after colon) or None.
    """
    if not bookmark_line.strip():
        return None
    # Get everything after the colon
    _, _, after_colon = bookmark_line.partition(":")
    if not after_colon:
        return None
    # First whitespace-separated field is the change_id
    parts = after_colon.strip().split()
    return parts[0] if parts else None


def validate_url_scheme(url: str) -> bool:
    """Check if URL uses an allowed scheme."""
    return any(url.startswith(scheme) for scheme in ALLOWED_URL_SCHEMES)


def is_path_under_root(path: Path, root: Path) -> bool:
    """Check if path is safely under root directory.

    Uses proper path containment check (not string prefix matching)
    to prevent path traversal attacks.
    """
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


async def run_with_timeout(
    args: list[str],
    cwd: Path | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> tuple[int, str, str]:
    """Run a command with timeout and return (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode or 0, stdout.decode(), stderr.decode()
    except asyncio.TimeoutError:
        proc.kill()
        try:
            await asyncio.wait_for(proc.wait(), timeout=KILL_WAIT_TIMEOUT)
        except asyncio.TimeoutError:
            pass  # Give up waiting for zombie process
        return -1, "", f"Command timed out after {timeout}s"


async def run_jj(
    args: list[str],
    cwd: Path,
    timeout: float = DEFAULT_TIMEOUT,
) -> tuple[int, str, str]:
    """Run a jj command and return (returncode, stdout, stderr)."""
    return await run_with_timeout(["jj", "--no-pager", *args], cwd, timeout)


async def get_git_remotes(repo_path: Path) -> dict[str, str]:
    """Get all git remotes for a jj repository.

    Returns dict mapping remote name to URL. Handles stale workspaces.
    """
    returncode, stdout, stderr = await run_jj(["git", "remote", "list"], repo_path)

    # Handle stale workspace
    if returncode != 0 and "stale" in stderr.lower():
        success, _ = await update_stale_workspace(repo_path)
        if success:
            returncode, stdout, stderr = await run_jj(
                ["git", "remote", "list"], repo_path
            )

    if returncode != 0:
        return {}

    remotes: dict[str, str] = {}
    for line in stdout.strip().split("\n"):
        if line:
            parts = line.split(maxsplit=1)
            if len(parts) >= 2:
                remotes[parts[0]] = parts[1]
    return remotes


async def update_stale_workspace(workspace_path: Path) -> tuple[bool, bool]:
    """Update a stale workspace and check for divergent commits.

    Returns (success, has_divergent) tuple.
    """
    returncode, stdout, stderr = await run_jj(
        ["workspace", "update-stale"], workspace_path
    )
    if returncode != 0:
        print(
            f"Warning: Failed to update stale workspace {workspace_path}: {stderr}",
            file=sys.stderr,
        )
        return False, False

    # Check for divergent commits after update
    returncode, stdout, _ = await run_jj(["log", "-r", "@"], workspace_path)
    has_divergent = "divergent" in stdout.lower() if returncode == 0 else False

    if has_divergent:
        print(
            f"Warning: Divergent commits detected in {workspace_path} after updating stale workspace",
            file=sys.stderr,
        )

    return True, has_divergent


async def get_current_state(workspace_path: Path) -> tuple[str, str, str | None]:
    """Get current change_id, commit_id, and bookmark for a workspace."""
    returncode, stdout, stderr = await run_jj(
        [
            "log",
            "-r",
            "@",
            "-T",
            r'change_id ++ "\n" ++ commit_id ++ "\n"',
            "--no-graph",
        ],
        workspace_path,
    )
    if returncode != 0:
        # Check if workspace is stale and try to update it
        if "stale" in stderr.lower():
            success, _ = await update_stale_workspace(workspace_path)
            if success:
                # Retry after updating
                returncode, stdout, stderr = await run_jj(
                    [
                        "log",
                        "-r",
                        "@",
                        "-T",
                        r'change_id ++ "\n" ++ commit_id ++ "\n"',
                        "--no-graph",
                    ],
                    workspace_path,
                )
        if returncode != 0:
            raise JJOutputError(f"Failed to get state for {workspace_path}: {stderr}")

    stdout = stdout.strip()
    if not stdout:
        raise JJOutputError(f"Empty output from jj log for {workspace_path}")

    lines = stdout.split("\n")
    if len(lines) < 2:
        raise JJOutputError(
            f"Expected 2 lines from jj log, got {len(lines)}: {stdout!r}"
        )

    change_id = lines[0].strip()
    commit_id = lines[1].strip()

    if not change_id:
        raise JJOutputError(f"Empty change_id for {workspace_path}")
    if not commit_id:
        raise JJOutputError(f"Empty commit_id for {workspace_path}")

    # Get bookmark pointing to current commit
    returncode, stdout, _ = await run_jj(["bookmark", "list"], workspace_path)
    bookmark = None
    if returncode == 0 and stdout.strip():
        for line in stdout.strip().split("\n"):
            if not line.strip():
                continue
            # Use exact field matching, not substring
            if extract_bookmark_change_id(line) == change_id:
                bookmark = extract_bookmark_name(line)
                break

    return change_id, commit_id, bookmark


async def get_uncommitted_changes(repo_path: Path) -> list[dict[str, str | None]]:
    """Get changes that haven't been pushed to remote."""
    returncode, stdout, _ = await run_jj(
        [
            "log",
            "-r",
            "remote_bookmarks()..@",
            "-T",
            r'change_id ++ "\t" ++ commit_id ++ "\t" ++ description.first_line() ++ "\n"',
            "--no-graph",
        ],
        repo_path,
    )
    if returncode != 0:
        return []

    if not stdout.strip():
        return []

    changes: list[dict[str, str | None]] = []

    # Get all bookmarks once
    bm_returncode, bm_stdout, _ = await run_jj(["bookmark", "list"], repo_path)
    bookmark_lines = (
        [line for line in bm_stdout.strip().split("\n") if line.strip()]
        if bm_returncode == 0
        else []
    )

    for line in stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("\t", 2)
        if len(parts) < 2:
            raise JJOutputError(
                f"Expected at least 2 tab-separated fields, got: {line!r}"
            )

        change_id = parts[0]
        commit_id = parts[1]
        description = parts[2] if len(parts) > 2 else ""

        if not change_id or not commit_id:
            raise JJOutputError(f"Empty change_id or commit_id in line: {line!r}")

        # Find bookmark for this change using exact field matching
        bookmark = None
        for bm_line in bookmark_lines:
            if extract_bookmark_change_id(bm_line) == change_id:
                # Only include if not fully synced with remote
                if "@" not in bm_line or "ahead by" in bm_line:
                    bookmark = extract_bookmark_name(bm_line)
                break

        changes.append(
            {
                "change_id": change_id,
                "commit_id": commit_id,
                "description": description,
                "bookmark": bookmark,
            }
        )

    return changes


def is_primary_repo(jj_dir: Path) -> bool:
    """Check if this is a primary repo (not a workspace)."""
    repo_path = jj_dir / "repo"
    return repo_path.is_dir()


async def get_workspace_names(repo_path: Path) -> list[str]:
    """Get workspace names for a repository."""
    returncode, stdout, _ = await run_jj(
        ["workspace", "list", "-T", r'name ++ "\n"'],
        repo_path,
    )
    if returncode != 0:
        return ["default"]

    names = [line.strip() for line in stdout.strip().split("\n") if line.strip()]
    return names or ["default"]


def resolve_primary_repo(jj_dir: Path) -> Path | None:
    """For a workspace .jj dir, resolve the primary repo path."""
    repo_file = jj_dir / "repo"
    if not repo_file.is_file():
        return None

    try:
        target = repo_file.read_text().strip()
        target_path = Path(target)
        # Resolve relative paths against the .jj directory
        if not target_path.is_absolute():
            target_path = (jj_dir / target).resolve()
        primary_jj = target_path.parent
        return primary_jj.parent
    except (OSError, IOError):
        return None


def find_jj_directories(
    root_dir: Path,
) -> tuple[dict[Path, Path], list[tuple[Path, Path]]]:
    """Find all .jj directories and categorize them.

    Returns:
        (primary_repos, workspace_dirs) where:
        - primary_repos: dict mapping resolved .jj/repo path -> workspace path
        - workspace_dirs: list of (workspace path, primary .jj/repo path) tuples
    """
    primary_repos: dict[Path, Path] = {}
    workspace_dirs: list[tuple[Path, Path]] = []

    for root, dirs, _files in os.walk(root_dir):
        root_path = Path(root)
        dirs[:] = [d for d in dirs if not should_skip_dir(d)]

        jj_dir = root_path / ".jj"
        if not jj_dir.exists():
            continue

        dirs.clear()

        if is_primary_repo(jj_dir):
            repo_store = (jj_dir / "repo").resolve()
            primary_repos[repo_store] = root_path
        else:
            primary_path = resolve_primary_repo(jj_dir)
            if primary_path:
                primary_store = (primary_path / ".jj" / "repo").resolve()
                workspace_dirs.append((root_path, primary_store))

    return primary_repos, workspace_dirs


# ============================================================================
# File Discovery and Filtering
# ============================================================================


def is_binary_file(path: Path) -> bool:
    """Check if file appears to be binary by looking for null bytes."""
    try:
        with open(path, "rb") as f:
            chunk = f.read(BINARY_CHECK_SIZE)
            return b"\x00" in chunk
    except (OSError, IOError):
        return True  # Treat unreadable files as binary


def make_file_entry(filepath: Path, root_dir: Path) -> FileEntry | None:
    """Create FileEntry from path, or None on error."""
    try:
        stat = filepath.stat()
        rel_path = filepath.relative_to(root_dir)
        return FileEntry(
            relative_path=str(rel_path),
            size_bytes=stat.st_size,
            mtime=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        )
    except (OSError, ValueError):
        return None


def should_backup_file(path: Path, max_size: int = FILE_MAX_SIZE) -> bool:
    """Check if a file should be backed up.

    Filters:
    - Not a symlink
    - Non-dotfile (name doesn't start with .)
    - Under size limit
    - Not binary (no null bytes in first 8KB)
    """
    if path.name.startswith("."):
        return False
    if path.is_symlink():
        return False
    try:
        stat = path.stat()
        if stat.st_size > max_size:
            return False
        if stat.st_size == 0:
            return True  # Empty files are fine
        return not is_binary_file(path)
    except (OSError, IOError):
        return False


def identify_workspace_tree_parents(root_dir: Path) -> set[Path]:
    """Find directories that contain jj workspaces but aren't workspaces themselves.

    A workspace tree parent:
    - Contains subdirectories with .jj/ folders
    - Does NOT itself have a .jj/ folder
    - Example: ~/pivot/ contains default/.jj/, compare-plots/.jj/, etc.
    """
    primary_repos, workspace_dirs = find_jj_directories(root_dir)

    # Collect all workspace paths
    workspace_paths: set[Path] = set()
    for repo_path in primary_repos.values():
        workspace_paths.add(repo_path)
    for ws_path, _ in workspace_dirs:
        workspace_paths.add(ws_path)

    # Find parent directories that contain workspaces
    tree_parents: set[Path] = set()
    for ws_path in workspace_paths:
        parent = ws_path.parent
        # Must be under root_dir and not itself a workspace
        if parent != root_dir and parent not in workspace_paths:
            # Verify the parent doesn't have its own .jj
            if not (parent / ".jj").exists():
                tree_parents.add(parent)

    return tree_parents


def collect_symlinks(
    root_dir: Path,
    workspace_paths: set[Path],
) -> list[SymlinkEntry]:
    """Collect symlinks where both link and target are within root_dir.

    Args:
        root_dir: Root directory to search
        workspace_paths: Paths to jj workspaces (skip internal symlinks)
    """
    symlinks: list[SymlinkEntry] = []

    for dirpath, dirnames, filenames in os.walk(root_dir):
        dir_path = Path(dirpath)

        # Skip workspace internals
        if dir_path in workspace_paths or any(
            dir_path.is_relative_to(ws) for ws in workspace_paths
        ):
            dirnames.clear()
            continue

        if should_skip_dir(dir_path.name):
            dirnames.clear()
            continue

        for name in filenames + dirnames:
            path = dir_path / name
            if not path.is_symlink():
                continue

            try:
                # Get target (resolve relative to symlink location)
                target = path.readlink()
                if target.is_absolute():
                    resolved_target = target
                else:
                    resolved_target = (path.parent / target).resolve()

                # Skip if target is outside root_dir
                try:
                    resolved_target.relative_to(root_dir)
                except ValueError:
                    continue  # Target outside root, skip

                # Skip if symlink itself is outside root_dir
                try:
                    rel_path = path.relative_to(root_dir)
                except ValueError:
                    continue

                # Skip dotfile symlinks (created by install.sh)
                if rel_path.parts[0].startswith("."):
                    continue

                # Store target relative to root_dir
                rel_target = resolved_target.relative_to(root_dir)

                # Skip symlinks inside .dotfiles (managed by install.sh)
                if str(rel_path).startswith(".dotfiles/") or str(rel_path).startswith(
                    ".dotfiles\\"
                ):
                    continue

                symlinks.append(
                    SymlinkEntry(
                        relative_path=str(rel_path),
                        target=str(rel_target),
                    )
                )
            except (OSError, ValueError):
                continue

    return symlinks


def collect_files_in_directory(
    directory: Path,
    exclude_paths: set[Path],
    base_for_relative: Path,
) -> list[FileEntry]:
    """Recursively collect files from a directory, excluding certain paths.

    Args:
        directory: Directory to collect files from
        exclude_paths: Paths to skip (jj workspaces)
        base_for_relative: Base path for computing relative paths
    """
    files: list[FileEntry] = []

    for root, dirs, filenames in os.walk(directory):
        root_path = Path(root)

        # Skip excluded paths
        if root_path in exclude_paths:
            dirs.clear()
            continue

        # Filter directories
        dirs[:] = [
            d
            for d in dirs
            if not should_skip_dir(d) and (root_path / d) not in exclude_paths
        ]

        for filename in filenames:
            filepath = root_path / filename
            if should_backup_file(filepath):
                entry = make_file_entry(filepath, base_for_relative)
                if entry:
                    files.append(entry)

    return files


def discover_files(
    root_dir: Path,
) -> tuple[list[FileEntry], list[SymlinkEntry]]:
    """Discover all files and symlinks to backup.

    Returns a tuple of (files, symlinks) with paths relative to root_dir.

    Collects:
    1. Non-dotfiles directly in root_dir (~/)
    2. Files at top level of workspace tree parents (e.g., ~/pivot/*.md)
    3. Non-jj subdirectories within workspace tree parents (e.g., ~/pivot/pro_critique/)
    4. Symlinks where both link and target are within root_dir
    """
    tree_parents = identify_workspace_tree_parents(root_dir)

    # Collect workspace paths for exclusion
    primary_repos, workspace_dirs = find_jj_directories(root_dir)
    workspace_paths: set[Path] = set()
    for repo_path in primary_repos.values():
        workspace_paths.add(repo_path)
    for ws_path, _ in workspace_dirs:
        workspace_paths.add(ws_path)

    files: list[FileEntry] = []

    # 1. Files directly in root_dir
    for item in root_dir.iterdir():
        if item.is_file() and should_backup_file(item):
            entry = make_file_entry(item, root_dir)
            if entry:
                files.append(entry)

    # 2. Tree parent contents
    for tree_parent in tree_parents:
        for item in tree_parent.iterdir():
            if item in workspace_paths:
                # Skip jj workspaces
                continue

            if item.is_file():
                # Top-level file in tree parent
                if should_backup_file(item):
                    entry = make_file_entry(item, root_dir)
                    if entry:
                        files.append(entry)
            elif item.is_dir() and not item.name.startswith("."):
                # Non-jj subdirectory - collect all files recursively
                subdir_files = collect_files_in_directory(
                    item,
                    exclude_paths=workspace_paths,
                    base_for_relative=root_dir,
                )
                files.extend(subdir_files)

    # 3. Collect symlinks
    symlinks = collect_symlinks(root_dir, workspace_paths)

    return files, symlinks


async def build_repo_data(
    repo_store: Path,
    primary_path: Path,
    workspace_dirs: list[tuple[Path, Path]],
) -> RepoData | None:
    """Build RepoData for a single repository."""
    remotes = await get_git_remotes(primary_path)
    if not remotes:
        print(f"Warning: Skipping {primary_path} - no git remotes", file=sys.stderr)
        return None

    # Filter to remotes with valid URL schemes
    valid_remotes = {
        name: url for name, url in remotes.items() if validate_url_scheme(url)
    }
    if not valid_remotes:
        print(
            f"Warning: Skipping {primary_path} - no remotes with supported URL schemes",
            file=sys.stderr,
        )
        return None

    repo_data = RepoData(remotes=valid_remotes)
    ws_names = await get_workspace_names(primary_path)

    # Add default workspace
    if "default" in ws_names:
        try:
            change_id, commit_id, bookmark = await get_current_state(primary_path)
            repo_data.workspaces["default"] = WorkspaceData(
                path=str(primary_path),
                current_change_id=change_id,
                current_commit_id=commit_id,
                current_bookmark=bookmark,
            )
        except (RuntimeError, JJOutputError) as e:
            print(f"Warning: {e}", file=sys.stderr)

    # Find additional workspaces
    for ws_path, ws_primary_store in workspace_dirs:
        if ws_primary_store != repo_store:
            continue

        returncode, stdout, _ = await run_jj(
            [
                "log",
                "-r",
                "@",
                "--ignore-working-copy",
                "-T",
                "working_copies",
                "--no-graph",
            ],
            ws_path,
        )
        if returncode != 0 or not stdout.strip():
            continue

        ws_name = stdout.strip().rstrip("@")
        if ws_name == "default":
            continue

        try:
            change_id, commit_id, bookmark = await get_current_state(ws_path)
            repo_data.workspaces[ws_name] = WorkspaceData(
                path=str(ws_path),
                current_change_id=change_id,
                current_commit_id=commit_id,
                current_bookmark=bookmark,
            )
        except (RuntimeError, JJOutputError) as e:
            print(f"Warning: {e}", file=sys.stderr)

    return repo_data if repo_data.workspaces else None


async def discover_repos(root_dir: Path) -> dict[str, RepoData]:
    """Discover all jj repositories under root_dir."""
    primary_repos, workspace_dirs = find_jj_directories(root_dir)

    repos: dict[str, RepoData] = {}
    tasks: list[tuple[Path, Path, Coroutine[Any, Any, RepoData | None]]] = []

    for repo_store, primary_path in primary_repos.items():
        tasks.append(
            (
                repo_store,
                primary_path,
                build_repo_data(repo_store, primary_path, workspace_dirs),
            )
        )

    results: list[RepoData | None | BaseException] = await asyncio.gather(
        *[t[2] for t in tasks], return_exceptions=True
    )

    for (repo_store, primary_path, _), result in zip(tasks, results):
        if isinstance(result, BaseException):
            print(
                f"Warning: Error processing {primary_path}: {result}", file=sys.stderr
            )
            continue
        if result is None:
            continue

        # Derive repo name from origin remote, or first available
        remotes = result.remotes
        primary_url = remotes.get("origin") or next(iter(remotes.values()))
        repo_name = primary_url.rstrip("/").split("/")[-1].removesuffix(".git")

        base_name = repo_name
        counter = 1
        while repo_name in repos:
            repo_name = f"{base_name}-{counter}"
            counter += 1

        repos[repo_name] = result

    return repos


async def generate_manifest(root_dir: Path, include_files: bool = True) -> Manifest:
    """Generate a manifest of the current environment state.

    Args:
        root_dir: Root directory for discovery (usually $HOME)
        include_files: Whether to include files section (default True)
    """
    repos = await discover_repos(root_dir)

    workspaces: dict[str, RepoData] = {}
    all_uncommitted: list[UncommittedChange] = []

    uncommitted_tasks: list[
        tuple[str, Coroutine[Any, Any, list[dict[str, str | None]]]]
    ] = []
    for repo_name, repo_data in repos.items():
        workspaces[repo_name] = repo_data

        default_ws = repo_data.workspaces.get("default")
        if default_ws:
            uncommitted_tasks.append(
                (repo_name, get_uncommitted_changes(Path(default_ws.path)))
            )

    uncommitted_results = await asyncio.gather(
        *[t[1] for t in uncommitted_tasks], return_exceptions=True
    )
    for (repo_name, _), result in zip(uncommitted_tasks, uncommitted_results):
        if isinstance(result, BaseException):
            print(
                f"Warning: Error getting uncommitted changes for {repo_name}: {result}",
                file=sys.stderr,
            )
            continue
        for change in result:
            all_uncommitted.append(
                UncommittedChange(
                    change_id=change["change_id"] or "",
                    commit_id=change["commit_id"] or "",
                    description=change["description"] or "",
                    bookmark=change["bookmark"],
                    workspace=repo_name,
                )
            )

    hostname = os.uname().nodename
    files_list: list[FileEntry] | None = None
    symlinks_list: list[SymlinkEntry] | None = None

    if include_files:
        files, symlinks = discover_files(root_dir)
        files_list = files if files else None
        symlinks_list = symlinks if symlinks else None

    return Manifest(
        version=2 if include_files else 1,
        captured_at=datetime.now(timezone.utc).isoformat(),
        hostname=hostname,
        root_dir=str(root_dir),
        workspaces=workspaces,
        uncommitted=all_uncommitted,
        files=files_list,
        symlinks=symlinks_list,
    )


def validate_manifest(data: dict[str, Any]) -> Manifest:
    """Validate and parse manifest data using Pydantic."""
    try:
        return Manifest.model_validate(data)
    except Exception as e:
        raise ValidationError(f"Invalid manifest: {e}") from e


def validate_restore_paths(
    manifest: Manifest,
    root_dir: Path,
) -> None:
    """Validate all paths in manifest are within root_dir.

    Raises RestoreError if any path escapes root_dir.
    This is called before any downloads to fail fast on invalid manifests.
    """
    # Check file paths
    if manifest.files:
        for entry in manifest.files:
            if ".." in entry.relative_path:
                raise RestoreError(f"File path contains '..': {entry.relative_path}")
            target = root_dir / entry.relative_path
            if not is_path_under_root(target, root_dir):
                raise RestoreError(f"File path escapes root_dir: {entry.relative_path}")

    # Check symlink paths
    if manifest.symlinks:
        for entry in manifest.symlinks:
            if ".." in entry.relative_path or ".." in entry.target:
                raise RestoreError(
                    f"Symlink path contains '..': {entry.relative_path} -> {entry.target}"
                )
            link = root_dir / entry.relative_path
            target = root_dir / entry.target
            if not is_path_under_root(link, root_dir):
                raise RestoreError(
                    f"Symlink path escapes root_dir: {entry.relative_path}"
                )
            if not is_path_under_root(target, root_dir):
                raise RestoreError(f"Symlink target escapes root_dir: {entry.target}")

    # Check workspace paths - validate ALL paths (not just absolute)
    for repo_name, repo_data in manifest.workspaces.items():
        for ws_name, ws_data in repo_data.list_workspaces():
            ws_path = Path(ws_data.path)
            if not ws_path.is_absolute():
                raise RestoreError(
                    f"Workspace path must be absolute: {ws_data.path} (in {repo_name}/{ws_name})"
                )
            if not is_path_under_root(ws_path, root_dir):
                raise RestoreError(f"Workspace path escapes root_dir: {ws_path}")


def restore_symlinks(
    symlinks: list[SymlinkEntry],
    root_dir: Path,
    force: bool = False,
) -> tuple[int, int, list[str]]:
    """Restore symlinks after all files/directories exist.

    Creates symlinks with relative paths to maintain portability.

    Args:
        symlinks: List of symlink entries to restore
        root_dir: Root directory for paths
        force: If True, overwrite existing files/symlinks

    Returns:
        Tuple of (successful, failed, skipped_paths) counts
    """
    successful = 0
    failed = 0
    skipped: list[str] = []

    for entry in symlinks:
        link_path = root_dir / entry.relative_path
        target_path = root_dir / entry.target

        # Validate both are within root_dir (defense in depth)
        if not is_path_under_root(link_path, root_dir):
            print(
                f"Skipping symlink outside root: {entry.relative_path}", file=sys.stderr
            )
            failed += 1
            continue
        if not is_path_under_root(target_path, root_dir):
            print(
                f"Skipping symlink with target outside root: {entry.target}",
                file=sys.stderr,
            )
            failed += 1
            continue

        # Check if file exists and handle accordingly
        if link_path.exists() or link_path.is_symlink():
            if not force:
                skipped.append(entry.relative_path)
                continue
            link_path.unlink()

        try:
            link_path.parent.mkdir(parents=True, exist_ok=True)

            # Create relative symlink for portability
            # Calculate relative path from link location to target
            rel_target = os.path.relpath(target_path, link_path.parent)
            link_path.symlink_to(rel_target)
            successful += 1
        except OSError as e:
            print(
                f"Failed to create symlink {entry.relative_path}: {e}", file=sys.stderr
            )
            failed += 1

    return successful, failed, skipped


async def clone_repo(
    repo_name: str,
    remotes: dict[str, str],
    default_path: Path,
    semaphore: asyncio.Semaphore,
) -> tuple[bool, str | None]:
    """Clone a repository with semaphore limiting concurrency.

    Clones from origin (or first remote), then adds any additional remotes.

    Returns (success, error_message) tuple.
    """
    async with semaphore:
        print(f"Cloning {repo_name} to {default_path}...", file=sys.stderr)
        default_path.parent.mkdir(parents=True, exist_ok=True)

        # Clone from origin, or first remote if no origin
        clone_url = remotes.get("origin") or next(iter(remotes.values()))

        returncode, _, stderr = await run_with_timeout(
            ["jj", "git", "clone", "--colocate", clone_url, str(default_path)],
            timeout=CLONE_TIMEOUT,
        )
        if returncode != 0:
            error_msg = f"Error cloning {repo_name}: {stderr}"
            # Clean up partial clone
            if default_path.exists():
                try:
                    shutil.rmtree(default_path)
                except OSError as e:
                    error_msg += f" (cleanup failed: {e})"
            return False, error_msg

        # Add additional remotes (jj git clone creates "origin" by default)
        for remote_name, remote_url in remotes.items():
            if remote_name == "origin":
                continue  # Already created by clone
            returncode, _, stderr = await run_jj(
                ["git", "remote", "add", remote_name, remote_url],
                default_path,
            )
            if returncode != 0:
                print(
                    f"Warning: Failed to add remote {remote_name} to {repo_name}: {stderr}",
                    file=sys.stderr,
                )

        return True, None


async def check_workspace_state(
    ws_path: Path, expected_change_id: str
) -> tuple[bool, bool]:
    """Check if workspace is on expected change and if there are divergent changes.

    Returns (is_correct, has_divergent) tuple.
    """
    try:
        current_change_id, _, _ = await get_current_state(ws_path)
        is_correct = current_change_id == expected_change_id

        # Check for divergent changes
        returncode, stdout, _ = await run_jj(["log", "-r", "@"], ws_path)
        has_divergent = "divergent" in stdout.lower() if returncode == 0 else False

        return is_correct, has_divergent
    except (JJOutputError, RuntimeError):
        return False, False


async def restore_workspace(
    ws_path: Path,
    change_id: str,
    ws_name: str,
) -> tuple[bool, str | None]:
    """Restore a workspace to a specific change ID using jj edit.

    Returns (success, error_message) tuple.
    """
    # Check current state first
    is_correct, has_divergent = await check_workspace_state(ws_path, change_id)

    if is_correct:
        print(
            f"Workspace {ws_name} already on correct change {change_id[:8]}",
            file=sys.stderr,
        )
        return True, None

    if has_divergent:
        print(
            f"Warning: Workspace {ws_name} has divergent changes, manual resolution may be needed",
            file=sys.stderr,
        )

    # Use jj edit for consistent behavior across all workspaces
    returncode, _, stderr = await run_jj(["edit", change_id], ws_path)
    if returncode != 0:
        return False, f"Could not restore {ws_name} to {change_id[:8]}: {stderr}"

    # Verify the edit worked
    is_correct, has_divergent = await check_workspace_state(ws_path, change_id)
    if not is_correct:
        return False, f"Workspace {ws_name} not on expected change after edit"

    if has_divergent:
        print(
            f"Warning: Divergent changes detected in {ws_name} after restore",
            file=sys.stderr,
        )

    return True, None


async def restore_from_manifest(manifest: Manifest, force: bool = False) -> list[str]:
    """Restore environment from a manifest.

    Returns list of error messages encountered during restore.
    """
    errors: list[str] = []
    workspaces = manifest.workspaces
    home = Path.home()
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_CLONES)

    # Collect paths that are outside home
    paths_outside_home: list[str] = []
    for repo_data in workspaces.values():
        for _ws_name, ws_data in repo_data.list_workspaces():
            path = Path(ws_data.path)
            if not is_path_under_root(path, home):
                paths_outside_home.append(ws_data.path)

    if paths_outside_home:
        if not force:
            raise RestoreError(
                f"Refusing to restore paths outside home directory:\n  "
                + "\n  ".join(paths_outside_home)
                + "\n\nUse --force to override this check."
            )
        else:
            print("Warning: Restoring paths outside home directory:", file=sys.stderr)
            for p in paths_outside_home:
                print(f"  {p}", file=sys.stderr)

    # First pass: clone primary repos
    clone_tasks: list[
        tuple[str, RepoData, Path, Coroutine[Any, Any, tuple[bool, str | None]]]
    ] = []
    repos_to_process: list[tuple[str, RepoData, Path]] = []

    for repo_name, repo_data in workspaces.items():
        remotes = repo_data.remotes
        if not remotes:
            errors.append(f"Skipping {repo_name} - no remotes")
            continue

        # Filter to valid URL schemes
        valid_remotes = {
            name: url for name, url in remotes.items() if validate_url_scheme(url)
        }
        if not valid_remotes:
            errors.append(
                f"Skipping {repo_name} - no remotes with supported URL schemes"
            )
            continue

        default_ws = repo_data.workspaces.get("default")
        if not default_ws:
            errors.append(f"Skipping {repo_name} - no default workspace")
            continue

        default_path = Path(default_ws.path)

        if default_path.exists():
            print(f"Repository {repo_name} exists at {default_path}", file=sys.stderr)
            repos_to_process.append((repo_name, repo_data, default_path))
        else:
            clone_tasks.append(
                (
                    repo_name,
                    repo_data,
                    default_path,
                    clone_repo(repo_name, valid_remotes, default_path, semaphore),
                )
            )

    # Run clones concurrently
    if clone_tasks:
        results = await asyncio.gather(
            *[t[3] for t in clone_tasks], return_exceptions=True
        )
        for (repo_name, repo_data, default_path, _), result in zip(
            clone_tasks, results
        ):
            if isinstance(result, BaseException):
                errors.append(f"Clone failed for {repo_name}: {result}")
            else:
                success, error_msg = result
                if success:
                    repos_to_process.append((repo_name, repo_data, default_path))
                elif error_msg:
                    errors.append(error_msg)

    # Restore default workspaces to correct change - PARALLEL
    default_restore_tasks: list[
        tuple[str, Coroutine[Any, Any, tuple[bool, str | None]]]
    ] = []
    for repo_name, repo_data, default_path in repos_to_process:
        default_ws = repo_data.workspaces.get("default")
        if default_ws and default_ws.current_change_id:
            default_restore_tasks.append(
                (
                    f"{repo_name}/default",
                    restore_workspace(
                        default_path,
                        default_ws.current_change_id,
                        f"{repo_name}/default",
                    ),
                )
            )

    if default_restore_tasks:
        results = await asyncio.gather(
            *[t[1] for t in default_restore_tasks], return_exceptions=True
        )
        for (ws_name, _), result in zip(default_restore_tasks, results):
            if isinstance(result, BaseException):
                errors.append(f"Restore failed for {ws_name}: {result}")
            else:
                success, error_msg = result
                if not success and error_msg:
                    errors.append(error_msg)

    # Second pass: create non-default workspaces - SERIAL per repo, PARALLEL across repos
    async def create_repo_workspaces(
        repo_name: str,
        repo_data: RepoData,
        default_path: Path,
    ) -> list[str]:
        """Create all non-default workspaces for a repo (serially to avoid jj conflicts)."""
        repo_errors: list[str] = []

        for ws_name, ws_data in repo_data.list_workspaces():
            if ws_name == "default":
                continue

            ws_path = Path(ws_data.path)
            change_id = ws_data.current_change_id

            if ws_path.exists():
                # Workspace exists, check if it needs restoration
                if change_id:
                    success, error_msg = await restore_workspace(
                        ws_path, change_id, f"{repo_name}/{ws_name}"
                    )
                    if not success and error_msg:
                        repo_errors.append(error_msg)
                continue

            print(f"Creating workspace {ws_name} at {ws_path}...", file=sys.stderr)
            ws_path.parent.mkdir(parents=True, exist_ok=True)

            returncode, _, stderr = await run_jj(
                ["workspace", "add", "--colocate", str(ws_path), "--name", ws_name],
                default_path,
            )
            if returncode != 0:
                repo_errors.append(
                    f"Error creating workspace {repo_name}/{ws_name}: {stderr}"
                )
                continue

            # Restore to correct change
            if change_id:
                success, error_msg = await restore_workspace(
                    ws_path, change_id, f"{repo_name}/{ws_name}"
                )
                if not success and error_msg:
                    repo_errors.append(error_msg)

        return repo_errors

    # Run workspace creation for each repo in parallel
    workspace_tasks = []
    for repo_name, repo_data, default_path in repos_to_process:
        if default_path.exists():
            workspace_tasks.append(
                create_repo_workspaces(repo_name, repo_data, default_path)
            )

    if workspace_tasks:
        results = await asyncio.gather(*workspace_tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                errors.append(f"Workspace creation error: {result}")
            elif isinstance(result, list):
                errors.extend(result)

    # Report uncommitted changes
    uncommitted = manifest.uncommitted
    if uncommitted:
        print(
            "\nNote: The following uncommitted changes were recorded:", file=sys.stderr
        )
        for change in uncommitted:
            desc = change.description or "(no description)"
            print(
                f"  - {change.workspace}: {change.change_id[:8]} - {desc}",
                file=sys.stderr,
            )
        print(
            "These may not exist if they were discarded before backup.", file=sys.stderr
        )

    print("\nRestore complete!", file=sys.stderr)
    return errors


def load_manifest(path: Path | None) -> Manifest:
    """Load and validate manifest from file or stdin."""
    try:
        if path:
            with open(path) as f:
                data: dict[str, Any] = json.load(f)
        else:
            if sys.stdin.isatty():
                raise ManifestError(
                    "No manifest provided. Use --manifest-file or pipe JSON to stdin."
                )
            data = json.load(sys.stdin)
    except FileNotFoundError:
        raise ManifestError(f"Manifest file not found: {path}")
    except PermissionError:
        raise ManifestError(f"Permission denied reading manifest: {path}")
    except IsADirectoryError:
        raise ManifestError(f"Expected file but got directory: {path}")
    except json.JSONDecodeError as e:
        source = str(path) if path else "stdin"
        raise ManifestError(f"Invalid JSON in manifest from {source}: {e}")

    return validate_manifest(data)


# ============================================================================
# S3 Operations (aioboto3 with tenacity retries)
# ============================================================================


def parse_s3_url(url: str, ensure_trailing_slash: bool = False) -> tuple[str, str]:
    """Parse s3://bucket/key into (bucket, key).

    Args:
        url: S3 URL in format s3://bucket/key
        ensure_trailing_slash: If True, ensure key ends with '/'

    Returns:
        Tuple of (bucket, key) with key normalized as POSIX path

    Raises:
        ValueError: If URL is invalid or bucket is empty
    """
    parsed = urlparse(url)
    if parsed.scheme != "s3":
        raise ValueError(f"Invalid S3 URL scheme (expected s3://): {url}")
    bucket = parsed.netloc
    if not bucket:
        raise ValueError(f"Empty bucket name in S3 URL: {url}")
    # Normalize path: remove leading slash, use POSIX format
    key = parsed.path.lstrip("/")
    if ensure_trailing_slash and key and not key.endswith("/"):
        key += "/"
    return bucket, key


def validate_relative_path(rel_path: str) -> str:
    """Validate a relative path is safe (no traversal, not absolute).

    Raises:
        ValueError: If path is unsafe
    """
    path = PurePosixPath(rel_path)
    if path.is_absolute():
        raise ValueError(f"Absolute path not allowed: {rel_path}")
    if ".." in path.parts:
        raise ValueError(f"Path traversal not allowed: {rel_path}")
    return rel_path


def build_s3_paths(
    s3_base: str, machine: str, backup_name: str
) -> tuple[str, str, str]:
    """Build backup, claude, and opencode S3 destination paths.

    Args:
        s3_base: Base S3 path (e.g., s3://bucket/users/sami@metr.org/)
        machine: Machine identifier
        backup_name: Backup name (e.g., "2026-01-20")

    Returns:
        Tuple of (backup_path, claude_path, opencode_path)
    """
    base = s3_base.rstrip("/")
    return (
        f"{base}/{machine}/{backup_name}/",
        f"{base}/claude-code/{machine}/",
        f"{base}/opencode/{machine}/",
    )


def count_results(
    results: list[bool | BaseException],
    error_prefix: str,
    errors_list: list[str] | None = None,
) -> tuple[int, int]:
    """Count successes and failures from asyncio.gather results.

    Args:
        results: List of results from gather with return_exceptions=True
        error_prefix: Prefix for error messages
        errors_list: If provided, append error messages to this list

    Returns:
        Tuple of (successful, failed) counts
    """
    successful = 0
    failed = 0
    for r in results:
        if r is True:
            successful += 1
        else:
            if isinstance(r, BaseException):
                msg = f"{error_prefix}: {r}"
                print(msg, file=sys.stderr)
                if errors_list is not None:
                    errors_list.append(msg)
            failed += 1
    return successful, failed


async def run_parallel_s3_ops(
    tasks: list[tuple[Any, ...]],
    operation: Callable[..., Coroutine[Any, Any, bool]],
    error_prefix: str,
    errors_list: list[str] | None = None,
) -> tuple[int, int]:
    """Execute S3 operations in parallel with semaphore limiting.

    Args:
        tasks: List of argument tuples for the operation (excluding s3_client)
        operation: Async function taking (s3_client, *task_args) -> bool
        error_prefix: Prefix for error messages
        errors_list: If provided, append error messages to this list

    Returns:
        (successful, failed) counts
    """
    if not tasks:
        return 0, 0

    session = aioboto3.Session()
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_S3_OPS)

    async def with_semaphore(s3: "S3Client", *args: Any) -> bool:
        async with semaphore:
            return await operation(s3, *args)

    async with session.client("s3") as s3:
        results = await asyncio.gather(
            *[with_semaphore(s3, *task) for task in tasks],
            return_exceptions=True,
        )

    return count_results(results, error_prefix, errors_list)


def parse_sessions_after(date_str: str) -> datetime:
    """Parse --sessions-after date string to datetime.

    Args:
        date_str: Date in YYYY-MM-DD format

    Returns:
        datetime with UTC timezone

    Raises:
        DevEnvError: If date format is invalid
    """
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        raise DevEnvError(f"Invalid date format '{date_str}'. Use YYYY-MM-DD.")


def should_retry_s3_error(exc: BaseException) -> bool:
    """Determine if an S3 error should be retried."""
    if isinstance(exc, ClientError):
        error_code = exc.response.get("Error", {}).get("Code", "")
        # Retry on transient errors only
        return error_code in ("429", "500", "503", "SlowDown", "ServiceUnavailable")
    # Retry on connection errors
    return isinstance(exc, (ConnectionError, TimeoutError, OSError))


def _s3_retry_callback(retry_state: RetryCallState) -> None:
    """Log retry attempts for S3 operations."""
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    print(
        f"Retry {retry_state.attempt_number} after error: {exc}",
        file=sys.stderr,
    )


S3_RETRY = retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception(should_retry_s3_error),
    before_sleep=_s3_retry_callback,
)


@S3_RETRY
async def s3_upload_file(
    s3_client: "S3Client",
    local_path: Path,
    bucket: str,
    key: str,
) -> bool:
    """Upload a single file to S3 with retries using streaming."""
    try:
        with open(local_path, "rb") as f:
            await s3_client.upload_fileobj(f, bucket, key)
        return True
    except ClientError as e:
        if not should_retry_s3_error(e):
            print(f"Error uploading {local_path}: {e}", file=sys.stderr)
            return False
        raise


@S3_RETRY
async def s3_upload_bytes(
    s3_client: "S3Client",
    bucket: str,
    key: str,
    data: bytes,
) -> bool:
    """Upload bytes to S3 with retries."""
    try:
        await s3_client.put_object(Bucket=bucket, Key=key, Body=data)
        return True
    except ClientError as e:
        if not should_retry_s3_error(e):
            print(f"Error uploading to {key}: {e}", file=sys.stderr)
            return False
        raise


STREAMING_CHUNK_SIZE = 8 * 1024 * 1024  # 8MB chunks for streaming


@S3_RETRY
async def s3_download_file(
    s3_client: "S3Client",
    bucket: str,
    key: str,
    local_path: Path,
    force: bool = False,
    chunk_size: int = STREAMING_CHUNK_SIZE,
) -> bool:
    """Download S3 object to local file with streaming.

    Uses chunked streaming to handle large files without loading them
    entirely into memory.

    Args:
        s3_client: S3 client
        bucket: S3 bucket
        key: S3 key
        local_path: Local file path
        force: If True, overwrite existing files
        chunk_size: Chunk size for streaming
    """
    # Check if file exists
    if local_path.exists() and not force:
        return False  # Indicate skipped

    try:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        response = await s3_client.get_object(Bucket=bucket, Key=key)

        async with aiofiles.open(local_path, "wb") as f:
            body = response["Body"]
            while True:
                chunk = await body.read(chunk_size)
                if not chunk:
                    break
                await f.write(chunk)
        return True
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        if error_code == "NoSuchKey":
            print(f"File not found in S3: {key}", file=sys.stderr)
            return False
        if not should_retry_s3_error(e):
            print(f"Error downloading {key}: {e}", file=sys.stderr)
            return False
        raise


@S3_RETRY
async def s3_list_objects(
    s3_client: "S3Client",
    bucket: str,
    prefix: str,
) -> list[S3Object]:
    """List objects in S3 with a given prefix."""
    objects: list[S3Object] = []
    paginator = s3_client.get_paginator("list_objects_v2")
    async for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            objects.append(obj)
    return objects


async def sync_files_to_s3(
    files: list[FileEntry],
    s3_base: str,
    home: Path,
    errors_list: list[str] | None = None,
) -> tuple[int, int]:
    """Upload all files in parallel using aioboto3.

    Returns (successful, failed) counts.
    """
    bucket, base_key = parse_s3_url(s3_base, ensure_trailing_slash=True)
    files_key = base_key + "files/"

    tasks: list[tuple[Path, str]] = []
    for f in files:
        rel_path = validate_relative_path(f.relative_path)
        local_path = home / rel_path
        s3_key = files_key + PurePosixPath(rel_path).as_posix()
        tasks.append((local_path, s3_key))

    async def upload_file(s3: "S3Client", local_path: Path, s3_key: str) -> bool:
        return await s3_upload_file(s3, local_path, bucket, s3_key)

    return await run_parallel_s3_ops(tasks, upload_file, "Upload error", errors_list)


async def restore_files_from_s3(
    files: list[FileEntry],
    s3_base: str,
    home: Path,
    force: bool = False,
    errors_list: list[str] | None = None,
) -> tuple[int, int, list[str]]:
    """Download all files in parallel using aioboto3.

    Returns (successful, failed, skipped_paths) counts.
    """
    bucket, base_key = parse_s3_url(s3_base, ensure_trailing_slash=True)
    files_key = base_key + "files/"

    tasks: list[tuple[str, Path]] = []
    skipped: list[str] = []

    for f in files:
        rel_path = validate_relative_path(f.relative_path)
        local_path = home / rel_path
        s3_key = files_key + PurePosixPath(rel_path).as_posix()

        # Check if file exists and we're not forcing
        if local_path.exists() and not force:
            skipped.append(rel_path)
            continue

        tasks.append((s3_key, local_path))

    async def download_file(s3: "S3Client", s3_key: str, local_path: Path) -> bool:
        return await s3_download_file(s3, bucket, s3_key, local_path, force=True)

    successful, failed = await run_parallel_s3_ops(
        tasks, download_file, "Download error", errors_list
    )
    return successful, failed, skipped


async def list_backups(s3_base: str, machine: str) -> list[str]:
    """List available backup names in S3 for a specific machine.

    Args:
        s3_base: S3 base path (e.g., s3://bucket/users/sami@metr.org/)
        machine: Machine identifier

    Returns:
        List of backup names (sorted)

    Raises:
        DevEnvError: If access is denied or bucket not found
    """
    base = s3_base.rstrip("/")
    machine_prefix = f"{base}/{machine}/"
    bucket, base_key = parse_s3_url(machine_prefix, ensure_trailing_slash=True)

    session = aioboto3.Session()
    backup_names: set[str] = set()

    try:
        async with session.client("s3") as s3:
            paginator = s3.get_paginator("list_objects_v2")
            async for page in paginator.paginate(
                Bucket=bucket, Prefix=base_key, Delimiter="/"
            ):
                for prefix in page.get("CommonPrefixes", []):
                    # Extract backup name from prefix like "users/sami@metr.org/devpod/2026-01-20/"
                    prefix_str = prefix.get("Prefix")
                    if prefix_str:
                        name = prefix_str[len(base_key) :].rstrip("/")
                        if name:
                            backup_names.add(name)
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        if error_code == "AccessDenied":
            raise DevEnvError(
                f"Access denied listing backups in s3://{bucket}/{base_key}. Check your AWS credentials and ListBucket permission."
            )
        if error_code == "NoSuchBucket":
            raise DevEnvError(f"Bucket not found: {bucket}")
        raise

    return sorted(backup_names)


def _should_sync_claude_path(rel_path: Path) -> bool:
    """Check if a path relative to claude dir should be synced.

    Returns True if the path matches any entry in CLAUDE_SYNC_PATHS.
    Matches exact files (e.g., ".claude.json") or paths under directories (e.g., "projects/...").
    """
    path_str = str(rel_path)
    for sync_path in CLAUDE_SYNC_PATHS:
        # Exact match (e.g., ".claude.json" or "plugins/installed_plugins.json")
        if path_str == sync_path:
            return True
        # Path is under a sync directory (e.g., "projects/foo/bar" under "projects")
        if path_str.startswith(sync_path + "/"):
            return True
    return False


async def sync_claude_dir_to_s3(
    local_claude_dir: Path,
    s3_destination: str,
    dry_run: bool = False,
    errors_list: list[str] | None = None,
) -> tuple[int, int]:
    """Sync Claude Code data to S3.

    Only syncs paths in CLAUDE_SYNC_PATHS (session data, not git-tracked config).

    Args:
        local_claude_dir: Local Claude directory (e.g., ~/.dotfiles/.claude)
        s3_destination: Full S3 path for claude data (e.g., s3://bucket/.../claude-code/machine/)
        dry_run: If True, print what would be uploaded without uploading
        errors_list: If provided, append error messages to this list

    Returns (successful, failed) counts.
    """
    bucket, base_key = parse_s3_url(s3_destination, ensure_trailing_slash=True)

    if not local_claude_dir.exists():
        return 0, 0

    # Collect files that match CLAUDE_SYNC_PATHS
    local_files: list[tuple[Path, str]] = []
    for root, _dirs, files in os.walk(local_claude_dir):
        root_path = Path(root)
        for filename in files:
            filepath = root_path / filename
            rel_path = filepath.relative_to(local_claude_dir)

            # Only include files matching our whitelist
            if not _should_sync_claude_path(rel_path):
                continue

            # Use POSIX path for S3 key
            s3_key = base_key + PurePosixPath(rel_path).as_posix()
            local_files.append((filepath, s3_key))

    if not local_files:
        return 0, 0

    if dry_run:
        print(
            f"Would upload {len(local_files)} Claude Code files to s3://{bucket}/{base_key}",
            file=sys.stderr,
        )
        for filepath, s3_key in local_files[:10]:
            print(f"  {filepath.relative_to(local_claude_dir)}", file=sys.stderr)
        if len(local_files) > 10:
            print(f"  ... and {len(local_files) - 10} more", file=sys.stderr)
        return len(local_files), 0

    async def upload_file(s3: "S3Client", local_path: Path, s3_key: str) -> bool:
        return await s3_upload_file(s3, local_path, bucket, s3_key)

    return await run_parallel_s3_ops(
        local_files, upload_file, "Claude dir upload error", errors_list
    )


async def restore_claude_dir_from_s3(
    s3_source: str,
    local_claude_dir: Path,
    after: datetime | None = None,
    force: bool = False,
    dry_run: bool = False,
    errors_list: list[str] | None = None,
) -> tuple[int, int, int, list[str]]:
    """Restore Claude Code data from S3.

    Args:
        s3_source: Full S3 path for claude data (e.g., s3://bucket/.../claude-code/machine/)
        local_claude_dir: Local directory to restore to
        after: Only restore files modified after this datetime
        force: If True, overwrite existing files
        dry_run: If True, print what would be downloaded without downloading
        errors_list: If provided, append error messages to this list

    Returns (successful, failed, skipped_by_date, skipped_paths) counts.
    """
    bucket, base_key = parse_s3_url(s3_source, ensure_trailing_slash=True)

    # List all objects
    session = aioboto3.Session()
    async with session.client("s3") as s3:
        objects = await s3_list_objects(s3, bucket, base_key)

    # Filter by mtime if specified
    to_download: list[tuple[str, Path]] = []
    skipped_by_date = 0
    skipped_existing: list[str] = []

    for obj in objects:
        if after is not None:
            last_modified = obj.get("LastModified")
            if last_modified and last_modified < after:
                skipped_by_date += 1
                continue
        s3_key = obj.get("Key")
        if not s3_key:
            continue
        rel_path = s3_key[len(base_key) :]
        # Validate the path is safe
        if rel_path.startswith("/") or ".." in rel_path:
            print(f"Skipping unsafe path: {rel_path}", file=sys.stderr)
            skipped_by_date += 1
            continue
        local_file = local_claude_dir / rel_path

        # Check if file exists and we're not forcing
        if local_file.exists() and not force:
            skipped_existing.append(rel_path)
            continue

        to_download.append((s3_key, local_file))

    if not to_download:
        return 0, 0, skipped_by_date, skipped_existing

    if dry_run:
        print(
            f"Would download {len(to_download)} Claude Code files from s3://{bucket}/{base_key}",
            file=sys.stderr,
        )
        for s3_key, _ in to_download[:10]:
            rel_path = s3_key[len(base_key) :]
            print(f"  {rel_path}", file=sys.stderr)
        if len(to_download) > 10:
            print(f"  ... and {len(to_download) - 10} more", file=sys.stderr)
        return len(to_download), 0, skipped_by_date, skipped_existing

    async def download_file(s3: "S3Client", s3_key: str, local_path: Path) -> bool:
        return await s3_download_file(s3, bucket, s3_key, local_path, force=True)

    successful, failed = await run_parallel_s3_ops(
        to_download, download_file, "Claude dir download error", errors_list
    )
    return successful, failed, skipped_by_date, skipped_existing


def _should_sync_opencode_path(rel_path: Path) -> bool:
    """Check if a path relative to opencode storage dir should be synced."""
    if not rel_path.parts:
        return False
    return rel_path.parts[0] in OPENCODE_SYNC_DIRS


async def sync_opencode_dir_to_s3(
    local_opencode_dir: Path,
    s3_destination: str,
    dry_run: bool = False,
    errors_list: list[str] | None = None,
) -> tuple[int, int]:
    """Sync OpenCode session data to S3.

    Only syncs directories listed in OPENCODE_SYNC_DIRS.

    Returns (successful, failed) counts.
    """
    bucket, base_key = parse_s3_url(s3_destination, ensure_trailing_slash=True)

    if not local_opencode_dir.exists():
        return 0, 0

    local_files: list[tuple[Path, str]] = []
    for root, _dirs, files in os.walk(local_opencode_dir):
        root_path = Path(root)
        for filename in files:
            filepath = root_path / filename
            if filepath.is_symlink():
                continue
            rel_path = filepath.relative_to(local_opencode_dir)

            if not _should_sync_opencode_path(rel_path):
                continue

            s3_key = base_key + PurePosixPath(rel_path).as_posix()
            local_files.append((filepath, s3_key))

    if not local_files:
        return 0, 0

    if dry_run:
        print(
            f"Would upload {len(local_files)} OpenCode files to s3://{bucket}/{base_key}",
            file=sys.stderr,
        )
        for filepath, _s3_key in local_files[:10]:
            print(f"  {filepath.relative_to(local_opencode_dir)}", file=sys.stderr)
        if len(local_files) > 10:
            print(f"  ... and {len(local_files) - 10} more", file=sys.stderr)
        return len(local_files), 0

    async def upload_file(s3: "S3Client", local_path: Path, s3_key: str) -> bool:
        return await s3_upload_file(s3, local_path, bucket, s3_key)

    return await run_parallel_s3_ops(
        local_files, upload_file, "OpenCode dir upload error", errors_list
    )


async def restore_opencode_dir_from_s3(
    s3_source: str,
    local_opencode_dir: Path,
    after: datetime | None = None,
    force: bool = False,
    dry_run: bool = False,
    errors_list: list[str] | None = None,
) -> tuple[int, int, int, list[str]]:
    """Restore OpenCode session data from S3.

    Returns (successful, failed, skipped_by_date, skipped_paths) counts.
    """
    bucket, base_key = parse_s3_url(s3_source, ensure_trailing_slash=True)

    session = aioboto3.Session()
    async with session.client("s3") as s3:
        objects = await s3_list_objects(s3, bucket, base_key)

    to_download: list[tuple[str, Path]] = []
    skipped_by_date = 0
    skipped_existing: list[str] = []

    for obj in objects:
        if after is not None:
            last_modified = obj.get("LastModified")
            if last_modified and last_modified < after:
                skipped_by_date += 1
                continue
        s3_key = obj.get("Key")
        if not s3_key or s3_key.endswith("/"):
            continue
        rel_path = s3_key[len(base_key) :]
        if rel_path.startswith("/") or ".." in rel_path:
            print(f"Skipping unsafe path: {rel_path}", file=sys.stderr)
            skipped_by_date += 1
            continue
        if not _should_sync_opencode_path(Path(rel_path)):
            continue
        local_file = local_opencode_dir / rel_path

        if local_file.exists() and not force:
            skipped_existing.append(rel_path)
            continue

        to_download.append((s3_key, local_file))

    if not to_download:
        return 0, 0, skipped_by_date, skipped_existing

    if dry_run:
        print(
            f"Would download {len(to_download)} OpenCode files from s3://{bucket}/{base_key}",
            file=sys.stderr,
        )
        for s3_key, _ in to_download[:10]:
            rel_path = s3_key[len(base_key) :]
            print(f"  {rel_path}", file=sys.stderr)
        if len(to_download) > 10:
            print(f"  ... and {len(to_download) - 10} more", file=sys.stderr)
        return len(to_download), 0, skipped_by_date, skipped_existing

    async def download_file(s3: "S3Client", s3_key: str, local_path: Path) -> bool:
        return await s3_download_file(s3, bucket, s3_key, local_path, force=True)

    successful, failed = await run_parallel_s3_ops(
        to_download, download_file, "OpenCode dir download error", errors_list
    )
    return successful, failed, skipped_by_date, skipped_existing


async def run_backup(
    s3_base: str,
    backup_name: str,
    machine: str,
    manifest: Manifest,
    claude_dir_source: Path,
    opencode_dir_source: Path,
    dry_run: bool = False,
) -> list[str]:
    """Run full backup.

    Uploads to:
    - Backup: {s3_base}/{machine}/{backup_name}/
    - Claude: {s3_base}/claude-code/{machine}/
    - OpenCode: {s3_base}/opencode/{machine}/

    Returns list of error messages.
    """
    errors: list[str] = []
    root_dir = Path(manifest.root_dir)

    # Construct S3 paths
    backup_destination, claude_destination, opencode_destination = build_s3_paths(
        s3_base, machine, backup_name
    )

    bucket, backup_key = parse_s3_url(backup_destination, ensure_trailing_slash=True)
    manifest_key = backup_key + "manifest.json"

    if dry_run:
        print(f"[DRY RUN] Would backup to:", file=sys.stderr)
        print(f"  Backup: s3://{bucket}/{backup_key}", file=sys.stderr)
        print(f"  Claude: {claude_destination}", file=sys.stderr)
        print(f"  OpenCode: {opencode_destination}", file=sys.stderr)
        print(f"  Manifest: {len(manifest.workspaces)} workspaces", file=sys.stderr)

        if manifest.files:
            print(
                f"  Files: {len(manifest.files)} files would be uploaded",
                file=sys.stderr,
            )

        if manifest.symlinks:
            print(
                f"  Symlinks: {len(manifest.symlinks)} symlinks recorded",
                file=sys.stderr,
            )

        if claude_dir_source.exists():
            await sync_claude_dir_to_s3(
                claude_dir_source, claude_destination, dry_run=True
            )
        if opencode_dir_source.exists():
            await sync_opencode_dir_to_s3(
                opencode_dir_source, opencode_destination, dry_run=True
            )
        return errors

    print(f"Backing up to s3://{bucket}/{backup_key}", file=sys.stderr)

    if manifest.files:
        successful, failed = await sync_files_to_s3(
            manifest.files, backup_destination, root_dir, errors
        )
        print(f"  Uploaded {successful} files ({failed} failed)", file=sys.stderr)

    if claude_dir_source.exists():
        uploaded, failed = await sync_claude_dir_to_s3(
            claude_dir_source, claude_destination, errors_list=errors
        )
        print(
            f"  Synced {uploaded} Claude Code files to {claude_destination} ({failed} failed)",
            file=sys.stderr,
        )

    if opencode_dir_source.exists():
        uploaded, failed = await sync_opencode_dir_to_s3(
            opencode_dir_source, opencode_destination, errors_list=errors
        )
        print(
            f"  Synced {uploaded} OpenCode files to {opencode_destination} ({failed} failed)",
            file=sys.stderr,
        )

    session = aioboto3.Session()
    async with session.client("s3") as s3:
        manifest_json = manifest.model_dump_json(indent=2, exclude_none=True)
        success = await s3_upload_bytes(
            s3, bucket, manifest_key, manifest_json.encode()
        )
        if not success:
            errors.append("Failed to upload manifest after retries")
            raise DevEnvError("Failed to upload manifest after retries")
        print("  Uploaded manifest.json", file=sys.stderr)

    print(f"\nBackup complete: s3://{bucket}/{backup_key}", file=sys.stderr)
    return errors

    print(f"Backing up to s3://{bucket}/{backup_key}", file=sys.stderr)

    # Upload order: files first, manifest last (atomic commit point)
    # If files fail, we don't upload manifest, so restore won't see partial backup

    # 1. Upload files if present
    if manifest.files:
        successful, failed = await sync_files_to_s3(
            manifest.files, backup_destination, root_dir, errors
        )
        print(f"  Uploaded {successful} files ({failed} failed)", file=sys.stderr)

    # 2. Sync Claude directory
    if claude_dir_source.exists():
        uploaded, failed = await sync_claude_dir_to_s3(
            claude_dir_source, claude_destination, errors_list=errors
        )
        print(
            f"  Synced {uploaded} Claude Code files to {claude_destination} ({failed} failed)",
            file=sys.stderr,
        )

    # 3. Upload manifest last (commit point) with retry
    session = aioboto3.Session()
    async with session.client("s3") as s3:
        manifest_json = manifest.model_dump_json(indent=2, exclude_none=True)
        success = await s3_upload_bytes(
            s3, bucket, manifest_key, manifest_json.encode()
        )
        if not success:
            errors.append("Failed to upload manifest after retries")
            raise DevEnvError("Failed to upload manifest after retries")
        print("  Uploaded manifest.json", file=sys.stderr)

    print(f"\nBackup complete: s3://{bucket}/{backup_key}", file=sys.stderr)
    return errors


async def run_restore(
    s3_base: str,
    backup_name: str,
    machine: str,
    claude_dir_destination: Path,
    opencode_dir_destination: Path,
    sessions_after: datetime | None,
    force: bool,
    dry_run: bool = False,
) -> list[str]:
    """Run full restore.

    Downloads from:
    - Backup: {s3_base}/{machine}/{backup_name}/
    - Claude: {s3_base}/claude-code/{machine}/
    - OpenCode: {s3_base}/opencode/{machine}/

    Returns list of error messages.
    """
    errors: list[str] = []

    # Construct S3 paths
    backup_source, claude_source, opencode_source = build_s3_paths(
        s3_base, machine, backup_name
    )

    bucket, backup_key = parse_s3_url(backup_source, ensure_trailing_slash=True)
    manifest_key = backup_key + "manifest.json"

    # Download manifest
    session = aioboto3.Session()
    async with session.client("s3") as s3:
        try:
            response = await s3.get_object(Bucket=bucket, Key=manifest_key)
            manifest_content = await response["Body"].read()
            manifest_data = json.loads(manifest_content.decode())
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "NoSuchKey":
                raise RestoreError(
                    f"Manifest not found at s3://{bucket}/{manifest_key}"
                )
            raise

    manifest = validate_manifest(manifest_data)
    root_dir = Path(manifest.root_dir)

    # Display agent instructions prominently if present
    if manifest.agent_instructions:
        print("\n=== AGENT INSTRUCTIONS ===", file=sys.stderr)
        print(manifest.agent_instructions, file=sys.stderr)
        print("===========================\n", file=sys.stderr)

    print("Restoring from backup:", file=sys.stderr)
    print(f"  Hostname: {manifest.hostname}", file=sys.stderr)
    print(f"  Captured: {manifest.captured_at}", file=sys.stderr)
    print(f"  Root dir: {root_dir}", file=sys.stderr)
    print(f"  Workspaces: {len(manifest.workspaces)}", file=sys.stderr)

    if dry_run:
        print(f"\n[DRY RUN] Would restore from:", file=sys.stderr)
        print(f"  Backup: {backup_source}", file=sys.stderr)
        print(f"  Claude: {claude_source}", file=sys.stderr)
        print(f"  OpenCode: {opencode_source}", file=sys.stderr)

        if manifest.files:
            print(
                f"  Files: {len(manifest.files)} files would be downloaded",
                file=sys.stderr,
            )
        if manifest.symlinks:
            print(
                f"  Symlinks: {len(manifest.symlinks)} symlinks would be created",
                file=sys.stderr,
            )

        await restore_claude_dir_from_s3(
            claude_source, claude_dir_destination, after=sessions_after, dry_run=True
        )
        await restore_opencode_dir_from_s3(
            opencode_source,
            opencode_dir_destination,
            after=sessions_after,
            dry_run=True,
        )
        return errors

    # Validate all restore paths upfront
    validate_restore_paths(manifest, root_dir)

    # Restore repos and workspaces
    restore_errors = await restore_from_manifest(manifest, force=force)
    errors.extend(restore_errors)

    # Restore files if present
    if manifest.files:
        print("\nRestoring files...", file=sys.stderr)
        successful, failed, skipped = await restore_files_from_s3(
            manifest.files, backup_source, root_dir, force=force, errors_list=errors
        )
        print(f"  Downloaded {successful} files ({failed} failed)", file=sys.stderr)
        if skipped:
            print(
                f"  Skipped {len(skipped)} existing files (use --force to overwrite)",
                file=sys.stderr,
            )

    # Restore symlinks last (after all files exist)
    if manifest.symlinks:
        print("\nRestoring symlinks...", file=sys.stderr)
        successful, failed, skipped = restore_symlinks(
            manifest.symlinks, root_dir, force=force
        )
        print(f"  Created {successful} symlinks ({failed} failed)", file=sys.stderr)
        if skipped:
            print(
                f"  Skipped {len(skipped)} existing symlinks (use --force to overwrite)",
                file=sys.stderr,
            )

    # Restore Claude directory
    print(f"\nRestoring Claude Code data from {claude_source}...", file=sys.stderr)
    (
        downloaded,
        failed,
        skipped_date,
        skipped_existing,
    ) = await restore_claude_dir_from_s3(
        claude_source,
        claude_dir_destination,
        after=sessions_after,
        force=force,
        errors_list=errors,
    )
    print(
        f"  Downloaded {downloaded} Claude Code files ({failed} failed, {skipped_date} skipped by date filter)",
        file=sys.stderr,
    )
    if skipped_existing:
        print(
            f"  Skipped {len(skipped_existing)} existing files (use --force to overwrite)",
            file=sys.stderr,
        )

    print(f"\nRestoring OpenCode data from {opencode_source}...", file=sys.stderr)
    (
        downloaded,
        failed,
        skipped_date,
        skipped_existing,
    ) = await restore_opencode_dir_from_s3(
        opencode_source,
        opencode_dir_destination,
        after=sessions_after,
        force=force,
        errors_list=errors,
    )
    print(
        f"  Downloaded {downloaded} OpenCode files ({failed} failed, {skipped_date} skipped by date filter)",
        file=sys.stderr,
    )
    if skipped_existing:
        print(
            f"  Skipped {len(skipped_existing)} existing files (use --force to overwrite)",
            file=sys.stderr,
        )

    # Print error summary if any
    if errors:
        print(f"\n=== ERRORS ({len(errors)}) ===", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        print("===========================", file=sys.stderr)

    return errors


async def cmd_manifest(args: argparse.Namespace) -> int:
    """Handle 'manifest' command."""
    include_files = not args.no_include_files
    manifest = await generate_manifest(args.root_dir, include_files=include_files)
    print(manifest.model_dump_json(indent=2, exclude_none=True))
    return 0


async def cmd_backup(args: argparse.Namespace) -> int:
    """Handle 'backup' command."""
    machine = validate_safe_name(args.machine or get_machine_name(), "Machine name")
    backup_name = validate_safe_name(args.name, "Backup name")
    include_files = not args.no_include_files
    manifest = await generate_manifest(Path.home(), include_files=include_files)

    # Add agent instructions if provided
    if args.agent_instructions:
        manifest.agent_instructions = args.agent_instructions

    errors = await run_backup(
        s3_base=args.base,
        backup_name=backup_name,
        machine=machine,
        manifest=manifest,
        claude_dir_source=args.claude_dir_source,
        opencode_dir_source=args.opencode_dir_source,
        dry_run=args.dry_run,
    )
    return 1 if errors else 0


async def cmd_list_backups(args: argparse.Namespace) -> int:
    """Handle 'list-backups' command."""
    machine = validate_safe_name(args.machine or get_machine_name(), "Machine name")
    backups = await list_backups(args.base, machine)
    if backups:
        print(f"Available backups for {machine}:")
        for name in backups:
            print(f"  {name}")
    else:
        print(f"No backups found for {machine}.")
    return 0


async def cmd_restore(args: argparse.Namespace) -> int:
    """Handle 'restore' command."""
    if args.base:
        machine = validate_safe_name(args.machine or get_machine_name(), "Machine name")

        # If no backup name, list available backups
        if not args.name:
            backups = await list_backups(args.base, machine)
            if backups:
                print(f"Available backups for {machine}:")
                for name in backups:
                    print(f"  {name}")
                print("\nUse --name to select a backup to restore.")
            else:
                print(f"No backups found for {machine}.")
            return 0

        # Validate backup name
        backup_name = validate_safe_name(args.name, "Backup name")

        # S3 restore
        sessions_after = (
            parse_sessions_after(args.sessions_after) if args.sessions_after else None
        )
        errors = await run_restore(
            s3_base=args.base,
            backup_name=backup_name,
            machine=machine,
            claude_dir_destination=args.claude_dir_destination,
            opencode_dir_destination=args.opencode_dir_destination,
            sessions_after=sessions_after,
            force=args.force,
            dry_run=args.dry_run,
        )
        return 1 if errors else 0
    elif args.manifest_file:
        # Local manifest restore (legacy)
        manifest = load_manifest(args.manifest_file)
        errors = await restore_from_manifest(manifest, force=args.force)
        return 1 if errors else 0
    else:
        # Try stdin
        manifest = load_manifest(None)
        errors = await restore_from_manifest(manifest, force=args.force)
        return 1 if errors else 0


def build_argument_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser."""
    parser = argparse.ArgumentParser(description="DevEnv Capture/Restore System")
    parser.add_argument(
        "--timeout",
        type=float,
        default=CLI_DEFAULT_TIMEOUT,
        help=f"Overall timeout in seconds (default: {CLI_DEFAULT_TIMEOUT}s)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # manifest subcommand
    manifest_parser = subparsers.add_parser("manifest", help="Generate manifest JSON")
    manifest_parser.add_argument(
        "--root-dir",
        type=Path,
        default=Path.home(),
        help="Root directory for discovery (default: $HOME)",
    )
    manifest_parser.add_argument(
        "--no-include-files",
        action="store_true",
        help="Exclude files section from manifest",
    )

    # backup subcommand
    backup_parser = subparsers.add_parser("backup", help="Backup to S3")
    backup_parser.add_argument(
        "--base",
        required=True,
        help="S3 base path (e.g., s3://bucket/users/sami@metr.org/)",
    )
    backup_parser.add_argument(
        "--name",
        default=datetime.now().strftime("%Y-%m-%d"),
        help="Backup name (default: today's date YYYY-MM-DD)",
    )
    backup_parser.add_argument(
        "--machine",
        default=None,
        help="Machine identifier (default: hostname)",
    )
    backup_parser.add_argument(
        "--agent-instructions",
        help="Freeform instructions for the restoring agent",
    )
    backup_parser.add_argument(
        "--claude-dir-source",
        type=Path,
        default=Path.home() / ".dotfiles" / ".claude",
        help="Local Claude directory (default: ~/.dotfiles/.claude)",
    )
    backup_parser.add_argument(
        "--opencode-dir-source",
        type=Path,
        default=OPENCODE_STORAGE_DIR,
        help=f"OpenCode storage directory (default: {OPENCODE_STORAGE_DIR})",
    )
    backup_parser.add_argument(
        "--no-include-files",
        action="store_true",
        help="Exclude files from backup",
    )
    backup_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be uploaded without uploading",
    )

    # list-backups subcommand
    list_parser = subparsers.add_parser("list-backups", help="List available backups")
    list_parser.add_argument(
        "--base",
        required=True,
        help="S3 base path (e.g., s3://bucket/users/sami@metr.org/)",
    )
    list_parser.add_argument(
        "--machine",
        default=None,
        help="Machine identifier (default: hostname)",
    )

    # restore subcommand
    restore_parser = subparsers.add_parser(
        "restore", help="Restore from S3 backup or manifest file"
    )
    restore_parser.add_argument(
        "--base",
        help="S3 base path (e.g., s3://bucket/users/sami@metr.org/)",
    )
    restore_parser.add_argument(
        "--name",
        help="Backup name to restore (if omitted with --base, lists available)",
    )
    restore_parser.add_argument(
        "--machine",
        default=None,
        help="Machine to restore from (default: hostname)",
    )
    restore_parser.add_argument(
        "--claude-dir-destination",
        type=Path,
        default=Path.home() / ".dotfiles" / ".claude",
        help="Local Claude directory (default: ~/.dotfiles/.claude)",
    )
    restore_parser.add_argument(
        "--opencode-dir-destination",
        type=Path,
        default=OPENCODE_STORAGE_DIR,
        help=f"OpenCode storage directory (default: {OPENCODE_STORAGE_DIR})",
    )
    restore_parser.add_argument(
        "--manifest-file",
        type=Path,
        help="Path to local manifest file (alternative to S3 restore)",
    )
    restore_parser.add_argument(
        "--sessions-after",
        help="Only restore sessions modified after this date (YYYY-MM-DD)",
    )
    restore_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing files (default: skip existing files)",
    )
    restore_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be downloaded without downloading",
    )

    return parser


async def async_main() -> int:
    """Main entry point."""
    parser = build_argument_parser()
    args = parser.parse_args()

    handlers: dict[str, Callable[[argparse.Namespace], Coroutine[Any, Any, int]]] = {
        "manifest": cmd_manifest,
        "backup": cmd_backup,
        "list-backups": cmd_list_backups,
        "restore": cmd_restore,
    }

    try:
        return await asyncio.wait_for(
            handlers[args.command](args),
            timeout=args.timeout,
        )
    except asyncio.TimeoutError:
        print(f"Error: Operation timed out after {args.timeout}s", file=sys.stderr)
        return 1
    except DevEnvError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def main() -> None:
    """Entry point."""
    sys.exit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
