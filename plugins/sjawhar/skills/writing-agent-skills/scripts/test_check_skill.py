"""Tests for check_skill.py: each limit it fails a skill on, and the files it does not measure.

Run: uvx pytest plugins/sjawhar/skills/writing-agent-skills/scripts
Each test builds a skill under tmp_path that passes every check but the one under test, runs the
script on it, and reads its exit status and FAIL lines.
"""

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "check_skill.py"
NAME = "demo-skill"
DESC = "Use when a test needs a skill that passes every check."


def head(desc: str = DESC) -> str:
    """The frontmatter and title of a SKILL.md."""
    return f"---\nname: {NAME}\ndescription: {desc}\n---\n\n# Demo skill\n\n"


GOOD = head() + "State the one fact the agent lacks.\n"


def text_of(lines: int, size: int, start: str = "", final_newline: bool = True) -> str:
    """`start`, then lines of x: `lines` lines by str.splitlines() and `size` UTF-8 bytes in all."""
    body = ["x"] * (lines - len(start.splitlines()))
    end = "\n" if final_newline else ""
    body[-1] += "x" * (size - len((start + "\n".join(body) + end).encode()))
    text = start + "\n".join(body) + end
    assert (len(text.splitlines()), len(text.encode())) == (lines, size)
    return text


def run(tmp_path: Path, files: dict[str, str], args: tuple[str, ...] = ()) -> tuple[int, list[str], str]:
    """Write the good skill with `files` over it, check it with `args`; return exit status, FAIL lines, output."""
    skill = tmp_path / NAME
    for rel, text in {"SKILL.md": GOOD, **files}.items():
        path = skill / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_bytes(text.encode())
    proc = subprocess.run([sys.executable, str(SCRIPT), *args, str(skill)], capture_output=True, text=True, check=False)
    fails = [line.strip() for line in proc.stdout.splitlines() if line.strip().startswith("FAIL ")]
    return proc.returncode, fails, proc.stdout + proc.stderr


def assert_passes(tmp_path: Path, files: dict[str, str], args: tuple[str, ...] = ()) -> None:
    code, fails, out = run(tmp_path, files, args)
    assert (code, fails) == (0, []), out


def assert_fails(tmp_path: Path, files: dict[str, str], fail: str, args: tuple[str, ...] = ()) -> None:
    code, fails, out = run(tmp_path, files, args)
    assert (code, fails) == (1, [f"FAIL {fail}"]), out


def desc_of(chars: int) -> str:
    return "Use when " + "x" * (chars - len("Use when "))


def test_a_good_skill_passes(tmp_path: Path) -> None:
    assert_passes(tmp_path, {})


def test_an_empty_description_fails(tmp_path: Path) -> None:
    assert_fails(tmp_path, {"SKILL.md": head("") + "x\n"}, "description is empty or missing")


def test_a_name_that_is_not_lowercase_words_joined_by_hyphens_fails(tmp_path: Path) -> None:
    skill_md = f"---\nname: Demo_Skill\ndescription: {DESC}\n---\n\nx\n"
    assert_fails(tmp_path, {"SKILL.md": skill_md}, "name 'Demo_Skill' is not lowercase words joined by hyphens")


def test_a_300_character_description_passes(tmp_path: Path) -> None:
    assert_passes(tmp_path, {"SKILL.md": head(desc_of(300)) + "x\n"})


def test_a_301_character_description_fails(tmp_path: Path) -> None:
    assert_fails(
        tmp_path,
        {"SKILL.md": head(desc_of(301)) + "x\n"},
        "description has 301 characters, over the 300-character limit",
    )


def test_a_description_that_starts_use_when_passes(tmp_path: Path) -> None:
    assert_passes(tmp_path, {"SKILL.md": head("Use when you measure a skill.") + "x\n"})


def test_a_description_that_starts_use_for_fails(tmp_path: Path) -> None:
    assert_fails(
        tmp_path,
        {"SKILL.md": head("Use for measuring a skill.") + "x\n"},
        'description does not start "Use when"',
    )


def test_a_description_that_starts_use_whenever_fails(tmp_path: Path) -> None:
    assert_fails(
        tmp_path,
        {"SKILL.md": head("Use whenever you measure a skill.") + "x\n"},
        'description does not start "Use when"',
    )


def test_a_double_quoted_description_on_two_lines_passes(tmp_path: Path) -> None:
    # YAML reads this value as "Use when a test needs a skill whose description wraps."
    desc = '"Use when a test needs a skill\n  whose description wraps."'
    assert_passes(tmp_path, {"SKILL.md": head(desc) + "x\n"})


def test_a_double_quoted_description_counts_the_characters_yaml_reads(tmp_path: Path) -> None:
    # As yaml.safe_dump writes a description with an em dash: \u2014 is one character, and a
    # backslash at a line's end joins the next line, whose "\ " is one space. YAML reads 300.
    desc = '"Use when ' + "x" * 60 + " \\u2014\\\n  \\ " + "y" * 228 + '"'
    assert_passes(tmp_path, {"SKILL.md": head(desc) + "x\n"})


def test_skill_md_of_150_lines_passes(tmp_path: Path) -> None:
    assert_passes(tmp_path, {"SKILL.md": text_of(150, 2_000, head())})


def test_skill_md_of_151_lines_fails(tmp_path: Path) -> None:
    assert_fails(
        tmp_path,
        {"SKILL.md": text_of(151, 2_000, head())},
        "SKILL.md has 151 lines, over the 150-line limit",
    )


def test_skill_md_of_151_lines_with_no_final_newline_fails(tmp_path: Path) -> None:
    assert_fails(
        tmp_path,
        {"SKILL.md": text_of(151, 2_000, head(), final_newline=False)},
        "SKILL.md has 151 lines, over the 150-line limit",
    )


def test_skill_md_of_8000_bytes_passes(tmp_path: Path) -> None:
    assert_passes(tmp_path, {"SKILL.md": text_of(20, 8_000, head())})


def test_skill_md_of_8001_bytes_fails(tmp_path: Path) -> None:
    assert_fails(
        tmp_path,
        {"SKILL.md": text_of(20, 8_001, head())},
        "SKILL.md has 8,001 bytes, over the 8,000-byte limit",
    )


def test_a_reference_of_150_lines_passes(tmp_path: Path) -> None:
    assert_passes(tmp_path, {"references/a.md": text_of(150, 2_000)})


def test_a_reference_of_151_lines_fails(tmp_path: Path) -> None:
    assert_fails(
        tmp_path,
        {"references/a.md": text_of(151, 2_000)},
        "references/a.md has 151 lines, over the 150-line limit",
    )


def test_a_reference_of_8000_bytes_passes(tmp_path: Path) -> None:
    assert_passes(tmp_path, {"references/a.md": text_of(20, 8_000)})


def test_a_reference_of_8001_bytes_fails(tmp_path: Path) -> None:
    assert_fails(
        tmp_path,
        {"references/a.md": text_of(20, 8_001)},
        "references/a.md has 8,001 bytes, over the 8,000-byte limit",
    )


def test_a_reference_two_levels_deep_is_measured(tmp_path: Path) -> None:
    assert_fails(
        tmp_path,
        {"references/a/b.md": text_of(20, 8_001)},
        "references/a/b.md has 8,001 bytes, over the 8,000-byte limit",
    )


def test_markdown_outside_references_is_measured(tmp_path: Path) -> None:
    assert_fails(
        tmp_path,
        {"agents/x.md": text_of(20, 8_001)},
        "agents/x.md has 8,001 bytes, over the 8,000-byte limit",
    )


def test_an_upper_case_md_extension_is_measured(tmp_path: Path) -> None:
    assert_fails(
        tmp_path,
        {"references/c.MD": text_of(20, 8_001)},
        "references/c.MD has 8,001 bytes, over the 8,000-byte limit",
    )


def test_a_markdown_extension_reference_is_measured(tmp_path: Path) -> None:
    assert_fails(
        tmp_path,
        {"references/d.markdown": text_of(20, 8_001)},
        "references/d.markdown has 8,001 bytes, over the 8,000-byte limit",
    )


def test_words_in_a_reference_do_not_make_it_a_template(tmp_path: Path) -> None:
    brief = text_of(20, 8_001, "Copy the brief below into the subagent's task.\n")
    assert_fails(
        tmp_path,
        {"references/brief.md": brief},
        "references/brief.md has 8,001 bytes, over the 8,000-byte limit",
    )


def test_a_template_under_assets_is_not_measured(tmp_path: Path) -> None:
    assert_passes(tmp_path, {"assets/brief.md": text_of(200, 12_000)})


def test_a_template_under_templates_is_not_measured(tmp_path: Path) -> None:
    assert_passes(tmp_path, {"templates/brief.md": text_of(200, 12_000)})


def test_markdown_under_references_templates_is_not_measured(tmp_path: Path) -> None:
    assert_passes(tmp_path, {"references/templates/x.md": text_of(200, 12_000)})


def test_a_script_is_not_measured(tmp_path: Path) -> None:
    assert_passes(tmp_path, {"scripts/tool.py": text_of(200, 12_000)})


def test_a_root_file_named_assets_is_measured(tmp_path: Path) -> None:
    assert_fails(
        tmp_path,
        {"ASSETS.md": text_of(20, 8_001)},
        "ASSETS.md has 8,001 bytes, over the 8,000-byte limit",
    )


def test_a_missing_linked_path_fails(tmp_path: Path) -> None:
    assert_fails(
        tmp_path,
        {"SKILL.md": GOOD + "Read `references/missing.md` before you start.\n"},
        "missing path: references/missing.md",
    )


def test_a_missing_template_path_fails(tmp_path: Path) -> None:
    assert_fails(
        tmp_path,
        {"SKILL.md": GOOD + "Copy `templates/missing.md` into the brief.\n"},
        "missing path: templates/missing.md",
    )


def old_copy(tmp_path: Path) -> tuple[str, ...]:
    """An old copy of the good skill, as the --old arguments that name it."""
    old = tmp_path / "old"
    old.mkdir()
    _ = (old / "SKILL.md").write_bytes(GOOD.encode())
    return ("--old", str(old))


def test_a_rewrite_that_grows_200_bytes_passes(tmp_path: Path) -> None:
    assert_passes(tmp_path, {"SKILL.md": GOOD + "x" * 199 + "\n"}, old_copy(tmp_path))


def test_a_rewrite_that_grows_201_bytes_fails(tmp_path: Path) -> None:
    old = len(GOOD.encode())
    assert_fails(
        tmp_path,
        {"SKILL.md": GOOD + "x" * 200 + "\n"},
        f"SKILL.md grew from {old} to {old + 201} bytes, over the 200-byte allowance",
        old_copy(tmp_path),
    )
