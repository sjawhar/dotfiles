#!/usr/bin/env python3
"""
Build SQLite index from Claude Code JSONL session files.

Design: DB is an index only - no content duplication. Content is read on-demand
from source JSONL files using line numbers. This avoids duplicating 8.5GB+ of data.

Usage:
    python index-sessions.py [--days N] [--db PATH]
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
    'interrupt': re.compile(r'\[Request interrupted by user', re.IGNORECASE),
    'rejection': re.compile(r"(doesn't want to proceed with this tool use|tool use was rejected)", re.IGNORECASE),
    'clarification': re.compile(r'^(no[,.\s!]|actually[,\s]|wait[,.\s!]|instead[,\s]|I meant|I said|that\'s not|not what I)', re.IGNORECASE),
}

# Secret detection patterns for redaction
SECRET_PATTERNS = [
    (re.compile(r'sk-[a-zA-Z0-9]{20,}'), '[REDACTED:api_key]'),
    (re.compile(r'ANTHROPIC_API_KEY\s*[=:]\s*\S+'), '[REDACTED:anthropic_key]'),
    (re.compile(r'OPENAI_API_KEY\s*[=:]\s*\S+'), '[REDACTED:openai_key]'),
    (re.compile(r'AWS_ACCESS_KEY_ID\s*[=:]\s*\S+'), '[REDACTED:aws_key]'),
    (re.compile(r'AWS_SECRET_ACCESS_KEY\s*[=:]\s*\S+'), '[REDACTED:aws_secret]'),
    (re.compile(r'GITHUB_TOKEN\s*[=:]\s*\S+'), '[REDACTED:github_token]'),
    (re.compile(r'ghp_[a-zA-Z0-9]{36,}'), '[REDACTED:github_pat]'),
    (re.compile(r'gho_[a-zA-Z0-9]{36,}'), '[REDACTED:github_oauth]'),
    (re.compile(r'password\s*[=:]\s*\S+', re.IGNORECASE), '[REDACTED:password]'),
    (re.compile(r'Bearer\s+[a-zA-Z0-9\-._~+/]+=*', re.IGNORECASE), '[REDACTED:bearer_token]'),
    (re.compile(r'-----BEGIN [A-Z ]+ PRIVATE KEY-----[\s\S]*?-----END [A-Z ]+ PRIVATE KEY-----'), '[REDACTED:private_key]'),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
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
"""


def get_projects_dir() -> Path:
    """Get the Claude projects directory."""
    return Path.home() / '.dotfiles' / '.claude' / 'projects'


def get_db_path() -> Path:
    """Get the default database path."""
    return Path.home() / '.dotfiles' / '.claude' / 'sessions.db'


def init_db(db_path: Path) -> sqlite3.Connection:
    """Initialize the SQLite database with schema."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA)
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
        return ''
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, dict):
                if item.get('type') == 'text':
                    texts.append(item.get('text', ''))
                elif item.get('type') == 'tool_result':
                    result_content = item.get('content', '')
                    if isinstance(result_content, str):
                        texts.append(result_content)
                    elif isinstance(result_content, list):
                        for sub in result_content:
                            if isinstance(sub, dict) and sub.get('type') == 'text':
                                texts.append(sub.get('text', ''))
            elif isinstance(item, str):
                texts.append(item)
        return '\n'.join(texts)
    return str(content)


def detect_flags(content: str, msg_type: str) -> list[str]:
    """Detect flags in message content.

    Returns list of flag types detected.
    """
    flags = []

    # Check interrupt and rejection in any message
    if FLAG_PATTERNS['interrupt'].search(content):
        flags.append('interrupt')
    if FLAG_PATTERNS['rejection'].search(content):
        flags.append('rejection')

    # Clarification only in user messages
    if msg_type == 'user':
        # Check at the start of the actual user text (not tool results)
        if FLAG_PATTERNS['clarification'].match(content.strip()):
            flags.append('clarification')

    return flags


def redact_secrets(text: str) -> str:
    """Redact sensitive content from text."""
    for pattern, replacement in SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def get_session_info(conn: sqlite3.Connection, session_id: str) -> Optional[dict]:
    """Get existing session info for staleness check."""
    cursor = conn.execute(
        "SELECT source_size FROM sessions WHERE id = ?",
        (session_id,)
    )
    row = cursor.fetchone()
    if row:
        return {'source_size': row[0]}
    return None


def index_session(conn: sqlite3.Connection, jsonl_path: Path, project: str) -> dict:
    """Index a single session JSONL file.

    Returns stats dict with counts.
    """
    session_id = jsonl_path.stem
    source_size = jsonl_path.stat().st_size

    # Check staleness
    existing = get_session_info(conn, session_id)
    if existing and existing['source_size'] == source_size:
        return {'skipped': True, 'reason': 'unchanged'}

    # Clear existing data for this session (re-index)
    if existing:
        conn.execute("DELETE FROM flags WHERE turn_id IN (SELECT id FROM turns WHERE session_id = ?)", (session_id,))
        conn.execute("DELETE FROM turns WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    turns_count = 0
    flags_count = 0
    session_timestamp = None
    initial_prompt_preview = None
    turn_number = 0

    with open(jsonl_path, 'r', encoding='utf-8', errors='replace') as f:
        for line_num, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue

            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                # Skip malformed lines
                continue

            msg_type = data.get('type')

            # Only index user and assistant turns
            if msg_type not in ('user', 'assistant'):
                continue

            turn_number += 1
            turns_count += 1

            # Get timestamp from first user message
            if msg_type == 'user' and session_timestamp is None:
                session_timestamp = data.get('timestamp')

            # Get initial prompt preview from first user message
            message = data.get('message', {})
            content = message.get('content') if isinstance(message, dict) else None
            text_content = extract_text_content(content)

            if msg_type == 'user' and initial_prompt_preview is None and text_content:
                initial_prompt_preview = text_content[:200]

            # Insert turn
            cursor = conn.execute(
                "INSERT INTO turns (session_id, turn_number, type, line_start, line_end) VALUES (?, ?, ?, ?, ?)",
                (session_id, turn_number, msg_type, line_num, line_num)
            )
            turn_id = cursor.lastrowid

            # Detect and insert flags
            detected_flags = detect_flags(text_content, msg_type)
            for flag_type in detected_flags:
                conn.execute(
                    "INSERT INTO flags (turn_id, flag_type) VALUES (?, ?)",
                    (turn_id, flag_type)
                )
                flags_count += 1

    # Insert session record
    conn.execute(
        "INSERT INTO sessions (id, project, timestamp, source_path, source_size, total_turns, initial_prompt_preview) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (session_id, project, session_timestamp, str(jsonl_path), source_size, turns_count, initial_prompt_preview)
    )

    return {
        'skipped': False,
        'turns': turns_count,
        'flags': flags_count
    }


def fetch_turn_content(source_path: str, line_start: int, line_end: int, redact: bool = True) -> str:
    """Fetch turn content from source file using line numbers.

    This is the on-demand content retrieval function.
    """
    try:
        with open(source_path, 'r', encoding='utf-8', errors='replace') as f:
            for i, line in enumerate(f, start=1):
                if i >= line_start and i <= line_end:
                    try:
                        data = json.loads(line)
                        message = data.get('message', {})
                        content = message.get('content') if isinstance(message, dict) else None
                        text = extract_text_content(content)
                        if redact:
                            text = redact_secrets(text)
                        return text
                    except json.JSONDecodeError:
                        return ''
                elif i > line_end:
                    break
    except FileNotFoundError:
        return ''
    return ''


def main():
    parser = argparse.ArgumentParser(description='Build SQLite index from Claude Code session JSONL files')
    parser.add_argument('--days', type=int, default=7, help='Only index sessions from the past N days (default: 7)')
    parser.add_argument('--db', type=str, default=None, help='Database path (default: ~/.dotfiles/.claude/sessions.db)')
    parser.add_argument('--all', action='store_true', help='Index all sessions regardless of age')
    parser.add_argument('--project', type=str, default=None, help='Only index a specific project')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')
    args = parser.parse_args()

    db_path = Path(args.db) if args.db else get_db_path()
    projects_dir = get_projects_dir()

    if not projects_dir.exists():
        print(f"Error: Projects directory not found: {projects_dir}", file=sys.stderr)
        sys.exit(1)

    # Calculate cutoff time
    cutoff_time = None
    if not args.all:
        cutoff_time = datetime.now(timezone.utc) - timedelta(days=args.days)

    # Initialize database
    conn = init_db(db_path)

    total_sessions = 0
    indexed_sessions = 0
    skipped_sessions = 0
    total_turns = 0
    total_flags = 0

    # Find all project directories
    if args.project:
        project_dirs = [projects_dir / args.project] if (projects_dir / args.project).exists() else []
    else:
        project_dirs = [d for d in projects_dir.iterdir() if d.is_dir()]

    for project_dir in sorted(project_dirs):
        project_name = project_dir.name

        # Find all JSONL files in the project
        jsonl_files = list(project_dir.glob('*.jsonl'))

        for jsonl_path in sorted(jsonl_files):
            total_sessions += 1

            # Skip if file is too old (based on mtime for quick filtering)
            if cutoff_time:
                file_mtime = datetime.fromtimestamp(jsonl_path.stat().st_mtime, tz=timezone.utc)
                if file_mtime < cutoff_time:
                    skipped_sessions += 1
                    continue

            # Index the session
            try:
                stats = index_session(conn, jsonl_path, project_name)

                if stats.get('skipped'):
                    skipped_sessions += 1
                    if args.verbose:
                        print(f"  Skipped (unchanged): {jsonl_path.name}")
                else:
                    indexed_sessions += 1
                    total_turns += stats.get('turns', 0)
                    total_flags += stats.get('flags', 0)
                    if args.verbose:
                        print(f"  Indexed: {jsonl_path.name} ({stats.get('turns', 0)} turns, {stats.get('flags', 0)} flags)")
            except Exception as e:
                print(f"  Error indexing {jsonl_path}: {e}", file=sys.stderr)
                continue

    conn.commit()
    conn.close()

    # Print summary
    print(f"\nIndexing complete:")
    print(f"  Database: {db_path}")
    print(f"  Sessions found: {total_sessions}")
    print(f"  Sessions indexed: {indexed_sessions}")
    print(f"  Sessions skipped: {skipped_sessions}")
    print(f"  Total turns indexed: {total_turns}")
    print(f"  Total flags detected: {total_flags}")


if __name__ == '__main__':
    main()
