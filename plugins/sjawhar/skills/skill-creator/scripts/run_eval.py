#!/usr/bin/env python3
"""Run trigger evaluation for a skill description.

Tests whether a skill's description causes the agent to trigger (read the skill)
for a set of queries. Outputs results as JSON.

Supports both OpenCode (`opencode run`) and Claude Code (`claude -p`).
Auto-detects which CLI is available, preferring OpenCode.
"""

import argparse
import json
import os
import select
import shutil
import subprocess
import sys
import time
import uuid
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from scripts.utils import parse_skill_md


def detect_cli() -> str:
    """Detect which CLI is available. Prefer opencode over claude."""
    if shutil.which("opencode"):
        return "opencode"
    if shutil.which("claude"):
        return "claude"
    raise RuntimeError(
        "Neither 'opencode' nor 'claude' found on PATH. "
        "Install one of them to run trigger evaluations."
    )


def find_project_root(cli: str) -> Path:
    """Find the project root by walking up from cwd.

    For OpenCode: looks for .opencode/ or AGENTS.md
    For Claude Code: looks for .claude/
    Falls back to cwd if nothing found.
    """
    current = Path.cwd()
    markers = (
        [".opencode", "AGENTS.md", ".claude"] if cli == "opencode" else [".claude"]
    )
    for parent in [current, *current.parents]:
        for marker in markers:
            target = parent / marker
            if target.exists():
                return parent
    return current


def _create_temp_skill_opencode(
    project_root: Path,
    clean_name: str,
    skill_name: str,
    skill_description: str,
) -> Path:
    """Create a temporary skill directory for OpenCode discovery.

    OpenCode discovers skills from .opencode/skills/<name>/SKILL.md
    """
    skill_dir = project_root / ".opencode" / "skills" / clean_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md = skill_dir / "SKILL.md"
    content = (
        f"---\n"
        f"name: {clean_name}\n"
        f"description: {skill_description}\n"
        f"---\n\n"
        f"# {skill_name}\n\n"
        f"This skill handles: {skill_description}\n"
    )
    skill_md.write_text(content)
    return skill_dir


def _create_temp_skill_claude(
    project_root: Path,
    clean_name: str,
    skill_name: str,
    skill_description: str,
) -> Path:
    """Create a temporary command file for Claude Code discovery.

    Claude Code discovers commands from .claude/commands/<name>.md
    """
    commands_dir = project_root / ".claude" / "commands"
    commands_dir.mkdir(parents=True, exist_ok=True)
    command_file = commands_dir / f"{clean_name}.md"
    # Use YAML block scalar to avoid breaking on quotes in description
    indented_desc = "\n  ".join(skill_description.split("\n"))
    content = (
        f"---\n"
        f"description: |\n"
        f"  {indented_desc}\n"
        f"---\n\n"
        f"# {skill_name}\n\n"
        f"This skill handles: {skill_description}\n"
    )
    command_file.write_text(content)
    return command_file


def _cleanup_temp_skill(path: Path, cli: str) -> None:
    """Remove temporary skill/command file."""
    if cli == "opencode":
        # Remove the entire temp skill directory
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
    else:
        # Remove single command file
        if path.exists():
            path.unlink()


def _run_opencode_query(
    query: str,
    clean_name: str,
    timeout: int,
    project_root: str,
    model: str | None = None,
) -> bool:
    """Run a query via `opencode run` and check if the skill was triggered.

    OpenCode JSON events have the shape:
      {"type": "tool_use", "part": {"tool": "skill", "state": {"input": {"name": "..."}}}}

    A skill is triggered if we see a tool_use event where tool=="skill"
    and the skill name matches our temp skill.
    """
    cmd = ["opencode", "run", "--format", "json", "--dir", project_root, query]
    if model:
        cmd.extend(["--model", model])

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )

    triggered = False
    start_time = time.time()
    buffer = ""

    try:
        while time.time() - start_time < timeout:
            if process.poll() is not None:
                remaining = process.stdout.read()
                if remaining:
                    buffer += remaining.decode("utf-8", errors="replace")
                break

            ready, _, _ = select.select([process.stdout], [], [], 1.0)
            if not ready:
                continue

            chunk = os.read(process.stdout.fileno(), 8192)
            if not chunk:
                break
            buffer += chunk.decode("utf-8", errors="replace")

            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line:
                    continue

                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # OpenCode tool_use events
                if event.get("type") == "tool_use":
                    part = event.get("part", {})
                    tool = part.get("tool", "")
                    state = part.get("state", {})
                    tool_input = state.get("input", {})

                    if tool == "skill":
                        invoked_name = tool_input.get("name", "")
                        if clean_name in invoked_name or invoked_name in clean_name:
                            return True
                    elif tool == "read":
                        file_path = tool_input.get("filePath", "")
                        if clean_name in file_path:
                            return True
                    else:
                        # First tool call is NOT skill — agent didn't trigger
                        return False

                # step_finish with reason "stop" means the turn ended
                elif event.get("type") == "step_finish":
                    reason = event.get("part", {}).get("reason", "")
                    if reason == "stop" and not triggered:
                        return False

        return triggered
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def _run_claude_query(
    query: str,
    clean_name: str,
    timeout: int,
    project_root: str,
    model: str | None = None,
) -> bool:
    """Run a query via `claude -p` and check if the skill was triggered.

    Uses Claude Code's stream-json format to detect Skill/Read tool calls.
    """
    cmd = [
        "claude",
        "-p",
        query,
        "--output-format",
        "stream-json",
        "--verbose",
        "--include-partial-messages",
    ]
    if model:
        cmd.extend(["--model", model])

    # Remove CLAUDECODE env var to allow nesting claude -p inside a
    # Claude Code session.
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        cwd=project_root,
        env=env,
    )

    triggered = False
    start_time = time.time()
    buffer = ""
    pending_tool_name = None
    accumulated_json = ""

    try:
        while time.time() - start_time < timeout:
            if process.poll() is not None:
                remaining = process.stdout.read()
                if remaining:
                    buffer += remaining.decode("utf-8", errors="replace")
                break

            ready, _, _ = select.select([process.stdout], [], [], 1.0)
            if not ready:
                continue

            chunk = os.read(process.stdout.fileno(), 8192)
            if not chunk:
                break
            buffer += chunk.decode("utf-8", errors="replace")

            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line:
                    continue

                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Early detection via stream events
                if event.get("type") == "stream_event":
                    se = event.get("event", {})
                    se_type = se.get("type", "")

                    if se_type == "content_block_start":
                        cb = se.get("content_block", {})
                        if cb.get("type") == "tool_use":
                            tool_name = cb.get("name", "")
                            if tool_name in ("Skill", "Read"):
                                pending_tool_name = tool_name
                                accumulated_json = ""
                            else:
                                return False

                    elif se_type == "content_block_delta" and pending_tool_name:
                        delta = se.get("delta", {})
                        if delta.get("type") == "input_json_delta":
                            accumulated_json += delta.get("partial_json", "")
                            if clean_name in accumulated_json:
                                return True

                    elif se_type in ("content_block_stop", "message_stop"):
                        if pending_tool_name:
                            return clean_name in accumulated_json
                        if se_type == "message_stop":
                            return False

                # Fallback: full assistant message
                elif event.get("type") == "assistant":
                    message = event.get("message", {})
                    for content_item in message.get("content", []):
                        if content_item.get("type") != "tool_use":
                            continue
                        tool_name = content_item.get("name", "")
                        tool_input = content_item.get("input", {})
                        if tool_name == "Skill" and clean_name in tool_input.get(
                            "skill", ""
                        ):
                            triggered = True
                        elif tool_name == "Read" and clean_name in tool_input.get(
                            "file_path", ""
                        ):
                            triggered = True
                        return triggered

                elif event.get("type") == "result":
                    return triggered

        return triggered
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def run_single_query(
    query: str,
    skill_name: str,
    skill_description: str,
    timeout: int,
    project_root: str,
    model: str | None = None,
    cli: str = "opencode",
) -> bool:
    """Run a single query and return whether the skill was triggered.

    Creates a temporary skill/command file so it appears in the agent's
    available skills list, runs the query, and checks if the skill was invoked.
    """
    unique_id = uuid.uuid4().hex[:8]
    clean_name = f"{skill_name}-skill-{unique_id}"

    if cli == "opencode":
        temp_path = _create_temp_skill_opencode(
            Path(project_root),
            clean_name,
            skill_name,
            skill_description,
        )
    else:
        temp_path = _create_temp_skill_claude(
            Path(project_root),
            clean_name,
            skill_name,
            skill_description,
        )

    try:
        if cli == "opencode":
            return _run_opencode_query(query, clean_name, timeout, project_root, model)
        else:
            return _run_claude_query(query, clean_name, timeout, project_root, model)
    finally:
        _cleanup_temp_skill(temp_path, cli)


def run_eval(
    eval_set: list[dict],
    skill_name: str,
    description: str,
    num_workers: int,
    timeout: int,
    project_root: Path,
    runs_per_query: int = 1,
    trigger_threshold: float = 0.5,
    model: str | None = None,
    cli: str = "opencode",
) -> dict:
    """Run the full eval set and return results."""
    results = []

    with ProcessPoolExecutor(max_workers=num_workers) as executor:
        future_to_info = {}
        for item in eval_set:
            for run_idx in range(runs_per_query):
                future = executor.submit(
                    run_single_query,
                    item["query"],
                    skill_name,
                    description,
                    timeout,
                    str(project_root),
                    model,
                    cli,
                )
                future_to_info[future] = (item, run_idx)

        query_triggers: dict[str, list[bool]] = {}
        query_items: dict[str, dict] = {}
        for future in as_completed(future_to_info):
            item, _ = future_to_info[future]
            query = item["query"]
            query_items[query] = item
            if query not in query_triggers:
                query_triggers[query] = []
            try:
                query_triggers[query].append(future.result())
            except Exception as e:
                print(f"Warning: query failed: {e}", file=sys.stderr)
                query_triggers[query].append(False)

    for query, triggers in query_triggers.items():
        item = query_items[query]
        trigger_rate = sum(triggers) / len(triggers)
        should_trigger = item["should_trigger"]
        if should_trigger:
            did_pass = trigger_rate >= trigger_threshold
        else:
            did_pass = trigger_rate < trigger_threshold
        results.append(
            {
                "query": query,
                "should_trigger": should_trigger,
                "trigger_rate": trigger_rate,
                "triggers": sum(triggers),
                "runs": len(triggers),
                "pass": did_pass,
            }
        )

    passed = sum(1 for r in results if r["pass"])
    total = len(results)

    return {
        "skill_name": skill_name,
        "description": description,
        "results": results,
        "summary": {
            "total": total,
            "passed": passed,
            "failed": total - passed,
        },
    }


def main():
    parser = argparse.ArgumentParser(
        description="Run trigger evaluation for a skill description"
    )
    parser.add_argument("--eval-set", required=True, help="Path to eval set JSON file")
    parser.add_argument("--skill-path", required=True, help="Path to skill directory")
    parser.add_argument(
        "--description", default=None, help="Override description to test"
    )
    parser.add_argument(
        "--num-workers", type=int, default=10, help="Number of parallel workers"
    )
    parser.add_argument(
        "--timeout", type=int, default=30, help="Timeout per query in seconds"
    )
    parser.add_argument(
        "--runs-per-query", type=int, default=3, help="Number of runs per query"
    )
    parser.add_argument(
        "--trigger-threshold", type=float, default=0.5, help="Trigger rate threshold"
    )
    parser.add_argument(
        "--model", default=None, help="Model to use (default: configured model)"
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Print progress to stderr"
    )
    parser.add_argument(
        "--cli",
        choices=["opencode", "claude", "auto"],
        default="auto",
        help="CLI to use (default: auto-detect, prefers opencode)",
    )
    args = parser.parse_args()

    eval_set = json.loads(Path(args.eval_set).read_text())
    skill_path = Path(args.skill_path)

    if not (skill_path / "SKILL.md").exists():
        print(f"Error: No SKILL.md found at {skill_path}", file=sys.stderr)
        sys.exit(1)

    cli = args.cli if args.cli != "auto" else detect_cli()
    name, original_description, content = parse_skill_md(skill_path)
    description = args.description or original_description
    project_root = find_project_root(cli)

    if args.verbose:
        print(f"CLI: {cli}", file=sys.stderr)
        print(f"Project root: {project_root}", file=sys.stderr)
        print(f"Evaluating: {description}", file=sys.stderr)

    output = run_eval(
        eval_set=eval_set,
        skill_name=name,
        description=description,
        num_workers=args.num_workers,
        timeout=args.timeout,
        project_root=project_root,
        runs_per_query=args.runs_per_query,
        trigger_threshold=args.trigger_threshold,
        model=args.model,
        cli=cli,
    )

    if args.verbose:
        summary = output["summary"]
        print(
            f"Results: {summary['passed']}/{summary['total']} passed", file=sys.stderr
        )
        for r in output["results"]:
            status = "PASS" if r["pass"] else "FAIL"
            rate_str = f"{r['triggers']}/{r['runs']}"
            print(
                f"  [{status}] rate={rate_str} expected={r['should_trigger']}: {r['query'][:70]}",
                file=sys.stderr,
            )

    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
