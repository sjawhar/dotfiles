#!/usr/bin/env python3
"""
Build SQLite index from Claude Code and OpenCode session files.

Design: DB is an index only - no content duplication. Content is read on-demand
from source files using line numbers (Claude Code) or file paths (OpenCode).

Sources:
  - Claude Code: ~/.dotfiles/.claude/projects/*/*.jsonl (one JSONL per session)
  - OpenCode: ~/.local/share/opencode/storage/ (structured directories)

Usage:
    python index-sessions.py [--days N] [--db PATH] [--source claude|opencode|all]
"""

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# Flag detection patterns
FLAG_PATTERNS = {
    "interrupt": re.compile(r"\[Request interrupted by user", re.IGNORECASE),
    "rejection": re.compile(
        r"(doesn't want to proceed with this tool use|tool use was rejected)",
        re.IGNORECASE,
    ),
    "clarification": re.compile(
        r"^(no[,.\s!]|actually[,\s]|wait[,.\s!]|instead[,\s]|I meant|I said|that\'s not|not what I)",
        re.IGNORECASE,
    ),
}

# Secret detection patterns for redaction
SECRET_PATTERNS = [
    (re.compile(r"sk-[a-zA-Z0-9]{20,}"), "[REDACTED:api_key]"),
    (re.compile(r"ANTHROPIC_API_KEY\s*[=:]\s*\S+"), "[REDACTED:anthropic_key]"),
    (re.compile(r"OPENAI_API_KEY\s*[=:]\s*\S+"), "[REDACTED:openai_key]"),
    (re.compile(r"AWS_ACCESS_KEY_ID\s*[=:]\s*\S+"), "[REDACTED:aws_key]"),
    (re.compile(r"AWS_SECRET_ACCESS_KEY\s*[=:]\s*\S+"), "[REDACTED:aws_secret]"),
    (re.compile(r"GITHUB_TOKEN\s*[=:]\s*\S+"), "[REDACTED:github_token]"),
    (re.compile(r"ghp_[a-zA-Z0-9]{36,}"), "[REDACTED:github_pat]"),
    (re.compile(r"gho_[a-zA-Z0-9]{36,}"), "[REDACTED:github_oauth]"),
    (re.compile(r"password\s*[=:]\s*\S+", re.IGNORECASE), "[REDACTED:password]"),
    (
        re.compile(r"Bearer\s+[a-zA-Z0-9\-._~+/]+=*", re.IGNORECASE),
        "[REDACTED:bearer_token]",
    ),
    (
        re.compile(
            r"-----BEGIN [A-Z ]+ PRIVATE KEY-----[\s\S]*?-----END [A-Z ]+ PRIVATE KEY-----"
        ),
        "[REDACTED:private_key]",
    ),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    source TEXT DEFAULT 'claude-code',
    project TEXT,
    timestamp TEXT,
    source_path TEXT,
    source_size INTEGER,
    total_turns INTEGER,
    initial_prompt_preview TEXT
);

CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    turn_number INTEGER,
    type TEXT,
    line_start INTEGER,
    line_end INTEGER,
    source_path TEXT,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE TABLE IF NOT EXISTS flags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    turn_id INTEGER,
    flag_type TEXT,
    FOREIGN KEY (turn_id) REFERENCES turns(id)
);

CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id);
CREATE INDEX IF NOT EXISTS idx_flags_turn ON flags(turn_id);
CREATE INDEX IF NOT EXISTS idx_sessions_timestamp ON sessions(timestamp);
CREATE INDEX IF NOT EXISTS idx_flags_type ON flags(flag_type);
CREATE INDEX IF NOT EXISTS idx_sessions_source ON sessions(source);
"""

MIGRATIONS = [
    # Add source column to sessions if missing (upgrade from v1)
    "ALTER TABLE sessions ADD COLUMN source TEXT DEFAULT 'claude-code'",
    # Add source_path column to turns if missing (for OpenCode per-file content)
    "ALTER TABLE turns ADD COLUMN source_path TEXT",
]


def get_projects_dir() -> Path:
    return Path.home() / ".dotfiles" / ".claude" / "projects"


def get_opencode_storage_dir() -> Path:
    return Path.home() / ".local" / "share" / "opencode" / "storage"


def get_db_path() -> Path:
    return Path.home() / ".dotfiles" / ".claude" / "sessions.db"


def init_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA)
    for migration in MIGRATIONS:
        try:
            conn.execute(migration)
        except sqlite3.OperationalError:
            pass  # Column already exists
    conn.commit()
    return conn


def extract_text_content(content) -> str:
    """Extract text content from message content field.

    Content can be:
    - A string (user's text message)
    - A list of content blocks (tool results, etc.)
    - None/empty
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    texts.append(item.get("text", ""))
                elif item.get("type") == "tool_result":
                    result_content = item.get("content", "")
                    if isinstance(result_content, str):
                        texts.append(result_content)
                    elif isinstance(result_content, list):
                        for sub in result_content:
                            if isinstance(sub, dict) and sub.get("type") == "text":
                                texts.append(sub.get("text", ""))
            elif isinstance(item, str):
                texts.append(item)
        return "\n".join(texts)
    return str(content)


def detect_flags(content: str, msg_type: str) -> list[str]:
    """Detect flags in message content.

    Returns list of flag types detected.
    """
    flags = []

    # Check interrupt and rejection in any message
    if FLAG_PATTERNS["interrupt"].search(content):
        flags.append("interrupt")
    if FLAG_PATTERNS["rejection"].search(content):
        flags.append("rejection")

    # Clarification only in user messages
    if msg_type == "user":
        # Check at the start of the actual user text (not tool results)
        if FLAG_PATTERNS["clarification"].match(content.strip()):
            flags.append("clarification")

    return flags


def redact_secrets(text: str) -> str:
    """Redact sensitive content from text."""
    for pattern, replacement in SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def get_session_info(conn: sqlite3.Connection, session_id: str) -> Optional[dict]:
    """Get existing session info for staleness check."""
    cursor = conn.execute(
        "SELECT source_size FROM sessions WHERE id = ?", (session_id,)
    )
    row = cursor.fetchone()
    if row:
        return {"source_size": row[0]}
    return None


def index_session(conn: sqlite3.Connection, jsonl_path: Path, project: str) -> dict:
    """Index a single session JSONL file.

    Returns stats dict with counts.
    """
    session_id = jsonl_path.stem
    source_size = jsonl_path.stat().st_size

    # Check staleness
    existing = get_session_info(conn, session_id)
    if existing and existing["source_size"] == source_size:
        return {"skipped": True, "reason": "unchanged"}

    # Clear existing data for this session (re-index)
    if existing:
        conn.execute(
            "DELETE FROM flags WHERE turn_id IN (SELECT id FROM turns WHERE session_id = ?)",
            (session_id,),
        )
        conn.execute("DELETE FROM turns WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    turns_count = 0
    flags_count = 0
    session_timestamp = None
    initial_prompt_preview = None
    turn_number = 0

    with open(jsonl_path, "r", encoding="utf-8", errors="replace") as f:
        for line_num, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue

            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                # Skip malformed lines
                continue

            msg_type = data.get("type")

            # Only index user and assistant turns
            if msg_type not in ("user", "assistant"):
                continue

            turn_number += 1
            turns_count += 1

            # Get timestamp from first user message
            if msg_type == "user" and session_timestamp is None:
                session_timestamp = data.get("timestamp")

            # Get initial prompt preview from first user message
            message = data.get("message", {})
            content = message.get("content") if isinstance(message, dict) else None
            text_content = extract_text_content(content)

            if msg_type == "user" and initial_prompt_preview is None and text_content:
                initial_prompt_preview = text_content[:200]

            # Insert turn
            cursor = conn.execute(
                "INSERT INTO turns (session_id, turn_number, type, line_start, line_end) VALUES (?, ?, ?, ?, ?)",
                (session_id, turn_number, msg_type, line_num, line_num),
            )
            turn_id = cursor.lastrowid

            # Detect and insert flags
            detected_flags = detect_flags(text_content, msg_type)
            for flag_type in detected_flags:
                conn.execute(
                    "INSERT INTO flags (turn_id, flag_type) VALUES (?, ?)",
                    (turn_id, flag_type),
                )
                flags_count += 1

    # Insert session record
    conn.execute(
        "INSERT INTO sessions (id, project, timestamp, source_path, source_size, total_turns, initial_prompt_preview) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            session_id,
            project,
            session_timestamp,
            str(jsonl_path),
            source_size,
            turns_count,
            initial_prompt_preview,
        ),
    )

    return {"skipped": False, "turns": turns_count, "flags": flags_count}


def fetch_turn_content(
    source_path: str, line_start: int, line_end: int, redact: bool = True
) -> str:
    """Fetch turn content from source file using line numbers.

    This is the on-demand content retrieval function.
    """
    try:
        with open(source_path, "r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f, start=1):
                if i >= line_start and i <= line_end:
                    try:
                        data = json.loads(line)
                        message = data.get("message", {})
                        content = (
                            message.get("content")
                            if isinstance(message, dict)
                            else None
                        )
                        text = extract_text_content(content)
                        if redact:
                            text = redact_secrets(text)
                        return text
                    except json.JSONDecodeError:
                        return ""
                elif i > line_end:
                    break
    except FileNotFoundError:
        return ""
    return ""


def load_opencode_project_map(storage_dir: Path) -> dict[str, str]:
    project_dir = storage_dir / "project"
    project_map: dict[str, str] = {}
    if not project_dir.exists():
        return project_map
    for pfile in project_dir.glob("*.json"):
        try:
            with open(pfile, "r", encoding="utf-8") as f:
                data = json.load(f)
            pid = data.get("id", pfile.stem)
            worktree = data.get("worktree", "")
            if worktree and worktree != "/":
                project_map[pid] = Path(worktree).name
            else:
                project_map[pid] = pid
        except (json.JSONDecodeError, OSError):
            continue
    return project_map


def extract_opencode_text_parts(storage_dir: Path, message_id: str) -> str:
    parts_dir = storage_dir / "part" / message_id
    if not parts_dir.exists():
        return ""
    texts = []
    for part_file in sorted(parts_dir.iterdir()):
        try:
            with open(part_file, "r", encoding="utf-8") as f:
                part = json.load(f)
            if part.get("type") == "text" and part.get("text"):
                texts.append(part["text"])
        except (json.JSONDecodeError, OSError):
            continue
    return "\n".join(texts)


def compute_opencode_session_size(storage_dir: Path, session_id: str) -> int:
    msg_dir = storage_dir / "message" / session_id
    if not msg_dir.exists():
        return 0
    total = 0
    for f in msg_dir.iterdir():
        total += f.stat().st_size
    return total


def index_opencode_session(
    conn: sqlite3.Connection,
    session_file: Path,
    project_name: str,
    storage_dir: Path,
) -> dict:
    try:
        with open(session_file, "r", encoding="utf-8") as f:
            session_data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"skipped": True, "reason": "unreadable"}

    session_id = session_data.get("id", session_file.stem)
    session_size = compute_opencode_session_size(storage_dir, session_id)

    existing = get_session_info(conn, session_id)
    if existing and existing["source_size"] == session_size:
        return {"skipped": True, "reason": "unchanged"}

    if existing:
        conn.execute(
            "DELETE FROM flags WHERE turn_id IN (SELECT id FROM turns WHERE session_id = ?)",
            (session_id,),
        )
        conn.execute("DELETE FROM turns WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    msg_dir = storage_dir / "message" / session_id
    if not msg_dir.exists():
        return {"skipped": True, "reason": "no messages"}

    msg_files = sorted(msg_dir.glob("*.json"))
    turns_count = 0
    flags_count = 0
    session_timestamp = None
    initial_prompt_preview = None
    turn_number = 0

    for msg_file in msg_files:
        try:
            with open(msg_file, "r", encoding="utf-8") as f:
                msg = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        msg_role = msg.get("role")
        if msg_role not in ("user", "assistant"):
            continue

        turn_number += 1
        turns_count += 1

        created_ms = msg.get("time", {}).get("created")
        if msg_role == "user" and session_timestamp is None and created_ms:
            session_timestamp = datetime.fromtimestamp(
                created_ms / 1000, tz=timezone.utc
            ).isoformat()

        text_content = extract_opencode_text_parts(storage_dir, msg.get("id", ""))

        if msg_role == "user" and initial_prompt_preview is None and text_content:
            initial_prompt_preview = text_content[:200]

        cursor = conn.execute(
            "INSERT INTO turns (session_id, turn_number, type, line_start, line_end, source_path) VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, turn_number, msg_role, 0, 0, str(msg_file)),
        )
        turn_id = cursor.lastrowid

        detected_flags = detect_flags(text_content, msg_role)
        for flag_type in detected_flags:
            conn.execute(
                "INSERT INTO flags (turn_id, flag_type) VALUES (?, ?)",
                (turn_id, flag_type),
            )
            flags_count += 1

    conn.execute(
        "INSERT INTO sessions (id, source, project, timestamp, source_path, source_size, total_turns, initial_prompt_preview) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            session_id,
            "opencode",
            project_name,
            session_timestamp,
            str(session_file),
            session_size,
            turns_count,
            initial_prompt_preview,
        ),
    )

    return {"skipped": False, "turns": turns_count, "flags": flags_count}


def fetch_opencode_turn_content(source_path: str, redact: bool = True) -> str:
    try:
        with open(source_path, "r", encoding="utf-8") as f:
            msg = json.load(f)
    except (json.JSONDecodeError, OSError, FileNotFoundError):
        return ""

    message_id = msg.get("id", "")
    storage_dir = Path(source_path).parent.parent.parent
    text = extract_opencode_text_parts(storage_dir, message_id)
    if redact:
        text = redact_secrets(text)
    return text


def main():
    parser = argparse.ArgumentParser(
        description="Build SQLite index from Claude Code and OpenCode session files"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Only index sessions from the past N days (default: 7)",
    )
    parser.add_argument(
        "--db",
        type=str,
        default=None,
        help="Database path (default: ~/.dotfiles/.claude/sessions.db)",
    )
    parser.add_argument(
        "--all", action="store_true", help="Index all sessions regardless of age"
    )
    parser.add_argument(
        "--project", type=str, default=None, help="Only index a specific project"
    )
    parser.add_argument(
        "--source",
        type=str,
        default="all",
        choices=["claude", "opencode", "all"],
        help="Which session source to index (default: all)",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    args = parser.parse_args()

    db_path = Path(args.db) if args.db else get_db_path()
    cutoff_time = None
    if not args.all:
        cutoff_time = datetime.now(timezone.utc) - timedelta(days=args.days)

    conn = init_db(db_path)

    total_sessions = 0
    indexed_sessions = 0
    skipped_sessions = 0
    total_turns = 0
    total_flags = 0

    # --- Claude Code sessions ---
    if args.source in ("claude", "all"):
        projects_dir = get_projects_dir()
        if projects_dir.exists():
            if args.project:
                project_dirs = (
                    [projects_dir / args.project]
                    if (projects_dir / args.project).exists()
                    else []
                )
            else:
                project_dirs = [d for d in projects_dir.iterdir() if d.is_dir()]

            for project_dir in sorted(project_dirs):
                project_name = project_dir.name
                jsonl_files = list(project_dir.glob("*.jsonl"))

                for jsonl_path in sorted(jsonl_files):
                    total_sessions += 1

                    if cutoff_time:
                        file_mtime = datetime.fromtimestamp(
                            jsonl_path.stat().st_mtime, tz=timezone.utc
                        )
                        if file_mtime < cutoff_time:
                            skipped_sessions += 1
                            continue

                    try:
                        stats = index_session(conn, jsonl_path, project_name)
                        if stats.get("skipped"):
                            skipped_sessions += 1
                            if args.verbose:
                                print(f"  Skipped (unchanged): {jsonl_path.name}")
                        else:
                            indexed_sessions += 1
                            total_turns += stats.get("turns", 0)
                            total_flags += stats.get("flags", 0)
                            if args.verbose:
                                print(
                                    f"  Indexed: {jsonl_path.name} ({stats.get('turns', 0)} turns, {stats.get('flags', 0)} flags)"
                                )
                    except Exception as e:
                        print(f"  Error indexing {jsonl_path}: {e}", file=sys.stderr)
                        continue
        elif args.source == "claude":
            print(
                f"Error: Projects directory not found: {projects_dir}",
                file=sys.stderr,
            )
            sys.exit(1)

    # --- OpenCode sessions ---
    if args.source in ("opencode", "all"):
        storage_dir = get_opencode_storage_dir()
        if storage_dir.exists():
            project_map = load_opencode_project_map(storage_dir)
            session_base = storage_dir / "session"

            if session_base.exists():
                for project_dir in sorted(session_base.iterdir()):
                    if not project_dir.is_dir():
                        continue
                    project_id = project_dir.name
                    project_name = project_map.get(project_id, project_id)

                    for session_file in sorted(project_dir.glob("*.json")):
                        total_sessions += 1

                        if cutoff_time:
                            file_mtime = datetime.fromtimestamp(
                                session_file.stat().st_mtime, tz=timezone.utc
                            )
                            if file_mtime < cutoff_time:
                                skipped_sessions += 1
                                continue

                        try:
                            stats = index_opencode_session(
                                conn, session_file, project_name, storage_dir
                            )
                            if stats.get("skipped"):
                                skipped_sessions += 1
                                if args.verbose:
                                    print(f"  Skipped (unchanged): {session_file.name}")
                            else:
                                indexed_sessions += 1
                                total_turns += stats.get("turns", 0)
                                total_flags += stats.get("flags", 0)
                                if args.verbose:
                                    print(
                                        f"  Indexed [opencode]: {session_file.name} ({stats.get('turns', 0)} turns, {stats.get('flags', 0)} flags)"
                                    )
                        except Exception as e:
                            print(
                                f"  Error indexing {session_file}: {e}",
                                file=sys.stderr,
                            )
                            continue
        elif args.source == "opencode":
            print(
                f"Error: OpenCode storage not found: {storage_dir}",
                file=sys.stderr,
            )
            sys.exit(1)

    conn.commit()
    conn.close()

    print(f"\nIndexing complete:")
    print(f"  Database: {db_path}")
    print(f"  Sessions found: {total_sessions}")
    print(f"  Sessions indexed: {indexed_sessions}")
    print(f"  Sessions skipped: {skipped_sessions}")
    print(f"  Total turns indexed: {total_turns}")
    print(f"  Total flags detected: {total_flags}")


if __name__ == "__main__":
    main()
