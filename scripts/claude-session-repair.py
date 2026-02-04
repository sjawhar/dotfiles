#!/usr/bin/env python3
"""
Claude Code session repair tool.

Repairs two types of session corruption:
1. Duplicate UUIDs in saved_hook_context entries
2. Orphan parent references (parentUuid points to non-existent UUID)

Usage:
    claude-session-repair.py <session.jsonl>           # Analyze and repair
    claude-session-repair.py <session.jsonl> --dry-run # Show what would be fixed
    claude-session-repair.py ~/.claude/projects/       # Scan all sessions
    claude-session-repair.py ~/.claude/projects/ -w 8  # Use 8 worker processes

Disable specific repairs:
    --no-fix-duplicates   Skip duplicate UUID repair
    --no-fix-orphans      Skip orphan parent repair

Performance features:
    - Multiprocessing for parallel triage
    - orjson for faster JSON (optional: pip install orjson)
"""

import argparse
import json
import os
import shutil
import sys
import uuid
from collections import Counter
from multiprocessing import Pool, cpu_count
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, TypedDict

if TYPE_CHECKING:
    type JsonDict = dict[str, Any]
else:
    JsonDict = dict


# Session entry with internal metadata fields added during processing
class SessionEntry(TypedDict, total=False):
    uuid: str
    parentUuid: str | None
    type: str
    # Internal fields added by this tool
    _line: int
    _raw: str


# Result type for process_session
type ProcessResult = Literal['healthy', 'repaired', 'would_repair', 'failed']


try:
    import orjson  # type: ignore[import-not-found]

    def _loads(data: bytes | str) -> JsonDict:
        return orjson.loads(data)  # type: ignore[no-any-return]

    def _dumps(obj: JsonDict) -> str:
        return orjson.dumps(obj, option=orjson.OPT_APPEND_NEWLINE).decode()  # type: ignore[attr-defined]

    _JsonDecodeError: type[Exception] = orjson.JSONDecodeError  # type: ignore[attr-defined]
except ImportError:
    def _loads(data: bytes | str) -> JsonDict:
        return json.loads(data)  # type: ignore[no-any-return]

    def _dumps(obj: JsonDict) -> str:
        return json.dumps(obj, ensure_ascii=False) + '\n'

    _JsonDecodeError = json.JSONDecodeError


def load_session(filepath: Path) -> list[SessionEntry]:
    """Load session entries from JSONL file."""
    entries: list[SessionEntry] = []
    with open(filepath, 'rb') as f:
        for i, line in enumerate(f):
            if line.strip():
                try:
                    entry: SessionEntry = _loads(line)  # type: ignore[assignment]
                    entry['_line'] = i + 1
                    entries.append(entry)
                except _JsonDecodeError as e:
                    print(f"Warning: Invalid JSON on line {i + 1}: {e}", file=sys.stderr)
                    entries.append({'_raw': line.decode(errors='replace'), '_line': i + 1})
    return entries


def quick_diagnose(filepath: Path) -> tuple[Path, bool, int]:
    """Quick check if file needs repair. Returns (path, needs_repair, entry_count)."""
    try:
        uuids: list[str] = []
        parents: list[str] = []
        with open(filepath, 'rb') as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    entry: JsonDict = _loads(line)
                    if (u := entry.get('uuid')) and isinstance(u, str):
                        uuids.append(u)
                    if (p := entry.get('parentUuid')) and isinstance(p, str):
                        parents.append(p)
                except Exception:
                    pass

        uuid_set = set(uuids)
        has_duplicates = len(uuids) != len(uuid_set)
        has_orphans = any(p not in uuid_set for p in parents)
        return (filepath, has_duplicates or has_orphans, len(uuids))
    except Exception:
        return (filepath, True, 0)


def triage_parallel(
    sessions: list[Path], workers: int | None = None
) -> tuple[list[Path], list[Path], int]:
    """Identify which sessions need repair using multiprocessing.

    Returns:
        Tuple of (needs_repair, healthy, total_entry_count)
    """
    if workers is None:
        workers = min(cpu_count(), len(sessions), 16)

    if workers <= 1 or len(sessions) < 4:
        results = [quick_diagnose(s) for s in sessions]
    else:
        with Pool(workers) as pool:
            results = pool.map(quick_diagnose, sessions)

    needs_repair: list[Path] = []
    healthy: list[Path] = []
    total = 0
    for path, needs_fix, count in results:
        total += count
        (needs_repair if needs_fix else healthy).append(path)
    return needs_repair, healthy, total


def get_uuid_stats(entries: list[SessionEntry]) -> tuple[set[str], set[str], int]:
    """Get UUID statistics. Returns (all_uuids, duplicate_uuids, orphan_count)."""
    uuid_counts: Counter[str | None] = Counter(
        e.get('uuid') for e in entries if e.get('uuid') and '_raw' not in e
    )
    all_uuids = {k for k in uuid_counts if k is not None}
    duplicates = {k for k, v in uuid_counts.items() if v > 1 and k is not None}
    orphan_count = sum(
        1 for e in entries if '_raw' not in e and (p := e.get('parentUuid')) and p not in all_uuids
    )
    return all_uuids, duplicates, orphan_count


def repair_duplicates(
    entries: list[SessionEntry], duplicates: set[str], dry_run: bool = False
) -> int:
    """Fix duplicate UUIDs in saved_hook_context entries."""
    if not duplicates:
        return 0

    seen: set[str] = set()
    fixed = 0
    for entry in entries:
        if '_raw' in entry:
            continue
        entry_uuid = entry.get('uuid')
        if entry_uuid in duplicates:
            if entry_uuid in seen and entry.get('type') == 'saved_hook_context':
                if not dry_run:
                    entry['uuid'] = str(uuid.uuid4())
                fixed += 1
            elif entry_uuid:
                seen.add(entry_uuid)
    return fixed


def repair_orphans(
    entries: list[SessionEntry], all_uuids: set[str], dry_run: bool = False
) -> int:
    """Fix orphan parent references by repointing to nearest valid ancestor."""
    fixed = 0
    for i, entry in enumerate(entries):
        if '_raw' in entry:
            continue
        parent = entry.get('parentUuid')
        if parent and parent not in all_uuids:
            # Find most recent valid entry before this one
            best_parent: str | None = None
            for j in range(i - 1, -1, -1):
                prev = entries[j]
                if '_raw' not in prev and (prev_uuid := prev.get('uuid')) in all_uuids:
                    best_parent = prev_uuid
                    break

            if not dry_run:
                entry['parentUuid'] = best_parent  # None if first entry
            fixed += 1
    return fixed


def process_session(filepath: Path, args: argparse.Namespace) -> ProcessResult:
    """Process a single session. Returns 'healthy', 'repaired', 'would_repair', or 'failed'."""
    if args.verbose:
        print(f"\nProcessing: {filepath}")

    entries = load_session(filepath)

    # Diagnose
    all_uuids, duplicates, orphan_count = get_uuid_stats(entries)
    dup_count = len(duplicates)

    needs_repair = (dup_count > 0 and not args.no_fix_duplicates) or (
        orphan_count > 0 and not args.no_fix_orphans
    )

    if args.verbose or args.dry_run:
        print(f"  Entries: {len(entries)}, Duplicate UUIDs: {dup_count}, Orphan parents: {orphan_count}")

    if not needs_repair:
        if args.verbose:
            print("  Status: healthy")
        return 'healthy'

    # Dry run
    if args.dry_run:
        dup_fixes = repair_duplicates(entries, duplicates, dry_run=True) if not args.no_fix_duplicates else 0
        orphan_fixes = repair_orphans(entries, all_uuids, dry_run=True) if not args.no_fix_orphans else 0
        print(f"  Would fix: {dup_fixes} duplicates, {orphan_fixes} orphans")
        return 'would_repair'

    # Backup (use copy2 to preserve timestamps for debugging)
    backup = filepath.with_suffix('.jsonl.bak')
    if backup.exists():
        print(f"  Skipping: backup exists at {backup}", file=sys.stderr)
        return 'failed'
    shutil.copy2(filepath, backup)

    # Repair
    dup_fixes = repair_duplicates(entries, duplicates) if not args.no_fix_duplicates else 0
    # Recompute UUID set after duplicate repair since new UUIDs were assigned
    if dup_fixes:
        all_uuids, _, _ = get_uuid_stats(entries)
    orphan_fixes = repair_orphans(entries, all_uuids) if not args.no_fix_orphans else 0

    # Validate - recompute stats to verify repairs worked
    _, post_duplicates, post_orphan_count = get_uuid_stats(entries)
    issues: list[str] = []
    if post_duplicates:
        issues.append(f"Still has {len(post_duplicates)} duplicate UUIDs")
    if post_orphan_count > 0:
        issues.append(f"Still has {post_orphan_count} orphan parents")

    if issues and not args.force:
        print(f"  Validation failed: {', '.join(issues)}", file=sys.stderr)
        shutil.copy2(backup, filepath)
        backup.unlink()
        return 'failed'

    # Write atomically
    temp = filepath.with_suffix('.jsonl.tmp')
    try:
        with open(temp, 'w') as f:
            for entry in entries:
                if '_raw' in entry:
                    f.write(entry['_raw'])
                else:
                    f.write(_dumps({k: v for k, v in entry.items() if not k.startswith('_')}))
        os.replace(temp, filepath)
    except OSError as e:
        print(f"  Write failed: {e}", file=sys.stderr)
        shutil.copy2(backup, filepath)
        if temp.exists():
            temp.unlink()
        return 'failed'

    if not args.keep_backup:
        backup.unlink()

    print(f"  Repaired: {dup_fixes} duplicates, {orphan_fixes} orphans")
    return 'repaired'


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Repair corrupted Claude Code session files',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('path', type=Path, help='Session file or directory to scan')
    parser.add_argument('--dry-run', '-n', action='store_true', help='Show what would be fixed')
    parser.add_argument('--verbose', '-v', action='store_true', help='Show detailed progress')
    parser.add_argument('--keep-backup', action='store_true', help='Keep .bak files after repair')
    parser.add_argument('--force', action='store_true', help='Repair even if validation fails')
    parser.add_argument('--no-fix-duplicates', action='store_true', help='Skip duplicate UUID repair')
    parser.add_argument('--no-fix-orphans', action='store_true', help='Skip orphan parent repair')
    parser.add_argument('--workers', '-w', type=int, help='Worker processes for triage (default: auto)')
    args = parser.parse_args()

    if not args.path.exists():
        print(f"Error: Path not found: {args.path}", file=sys.stderr)
        sys.exit(1)

    if args.path.is_file():
        process_session(args.path, args)
        return

    # Directory mode
    sessions = list(args.path.rglob('*.jsonl'))
    if not sessions:
        print(f"No .jsonl files found in {args.path}")
        return

    if args.verbose:
        print(f"Triaging {len(sessions)} sessions...")

    needs_repair, healthy, total = triage_parallel(sessions, args.workers)

    if args.verbose:
        print(f"  {len(healthy)} healthy, {len(needs_repair)} need processing ({total} entries)")

    if not needs_repair:
        print(f"Summary: 0 repaired, 0 failed, {len(healthy)} healthy")
        return

    results = [process_session(s, args) for s in needs_repair]
    repaired = results.count('repaired')
    would_repair = results.count('would_repair')
    failed = results.count('failed')

    if would_repair:
        print(f"\nSummary: {would_repair} would repair, {failed} failed, {len(healthy)} healthy")
    else:
        print(f"\nSummary: {repaired} repaired, {failed} failed, {len(healthy)} healthy")


if __name__ == '__main__':
    main()
