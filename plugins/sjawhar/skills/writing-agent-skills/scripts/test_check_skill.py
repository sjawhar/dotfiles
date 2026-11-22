"""Tests for check_skill.py: each limit it fails or warns a skill on, and the files it does not measure.

Run: uvx pytest plugins/sjawhar/skills/writing-agent-skills/scripts
Each test builds a skill under tmp_path that passes every check but the one under test, runs the
script on it, and reads its exit status and its FAIL or warn lines. The script runs with the
interpreter uv builds from its own `# /// script` block (it needs PyYAML and markdown-it-py),
never the one running the tests.
"""

import functools
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "check_skill.py"
NAME = "demo-skill"
DESC = "Use when a test needs a skill that passes every check."
WARN_41 = "warn 41 rules, over 40: split or delete"


def head(desc: str = DESC) -> str:
    """The frontmatter and title of a SKILL.md."""
    return f"---\nname: {NAME}\ndescription: {desc}\n---\n\n# Demo skill\n\n"


GOOD = head() + "State the one fact the agent lacks.\n"


def text_of(lines: int, size: int, start: str = "") -> str:
    """`start`, then lines of x: `lines` lines by str.splitlines() and `size` UTF-8 bytes in all."""
    body = ["x"] * (lines - len(start.splitlines()))
    body[-1] += "x" * (size - len((start + "\n".join(body) + "\n").encode()))
    text = start + "\n".join(body) + "\n"
    assert (len(text.splitlines()), len(text.encode())) == (lines, size)
    return text


def uv(*args: str) -> str:
    proc = subprocess.run(["uv", *args], capture_output=True, text=True, timeout=300, check=False)
    assert proc.returncode == 0, f"uv {' '.join(args)} exited {proc.returncode}: {proc.stderr}"
    return proc.stdout


@functools.cache
def script_python() -> str:
    """The interpreter uv builds for SCRIPT from its `# /// script` block."""
    # `find` only locates the environment; with none built it names a bare interpreter.
    _ = uv("sync", "--quiet", "--script", str(SCRIPT))
    return uv("python", "find", "--script", str(SCRIPT)).strip()


def run(tmp_path: Path, files: dict[str, str], args: tuple[str, ...] = ()) -> tuple[int, list[str], str]:
    """Write the good skill with `files` over it, check it with `args`; return exit status, FAIL lines, output."""
    skill = tmp_path / NAME
    for rel, text in {"SKILL.md": GOOD, **files}.items():
        path = skill / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_bytes(text.encode())
    proc = subprocess.run([script_python(), str(SCRIPT), *args, str(skill)], capture_output=True, text=True, check=False)
    fails = [line.strip() for line in proc.stdout.splitlines() if line.strip().startswith("FAIL ")]
    return proc.returncode, fails, proc.stdout + proc.stderr


def assert_passes(tmp_path: Path, files: dict[str, str]) -> None:
    code, fails, out = run(tmp_path, files)
    assert (code, fails) == (0, []), out


def assert_fails(tmp_path: Path, files: dict[str, str], fail: str) -> None:
    code, fails, out = run(tmp_path, files)
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


def test_frontmatter_that_is_not_valid_yaml_fails(tmp_path: Path) -> None:
    # No YAML reader accepts this frontmatter, so no host reads the skill's description.
    # check_skill.py names the SKILL.md line; PyYAML writes the text after the line number.
    skill_md = f'---\nname: {NAME}\ndescription: "Use when a test needs a skill\nargument-hint: "<file>"\n---\n\nx\n'
    code, fails, out = run(tmp_path, {"SKILL.md": skill_md})
    assert code == 1 and len(fails) == 1, out
    assert fails[0].startswith("FAIL SKILL.md frontmatter is not valid YAML at line 4: "), out


def test_frontmatter_that_is_a_yaml_list_fails(tmp_path: Path) -> None:
    assert_fails(tmp_path, {"SKILL.md": "---\n- a\n---\n\nx\n"}, "SKILL.md frontmatter is not a mapping")


def test_a_description_that_yaml_reads_as_a_number_fails(tmp_path: Path) -> None:
    assert_fails(tmp_path, {"SKILL.md": head("42") + "x\n"}, "description is not a string: YAML reads it as int")


def test_a_name_that_yaml_reads_as_a_list_fails(tmp_path: Path) -> None:
    skill_md = f"---\nname: [x]\ndescription: {DESC}\n---\n\nx\n"
    assert_fails(tmp_path, {"SKILL.md": skill_md}, "name is not a string: YAML reads it as list")


def test_a_300_line_skill_md_under_8000_bytes_passes(tmp_path: Path) -> None:
    assert_passes(tmp_path, {"SKILL.md": text_of(300, 2_000, head())})


def test_skill_md_of_8000_bytes_passes(tmp_path: Path) -> None:
    assert_passes(tmp_path, {"SKILL.md": text_of(20, 8_000, head())})


def test_skill_md_of_8001_bytes_fails(tmp_path: Path) -> None:
    assert_fails(
        tmp_path,
        {"SKILL.md": text_of(20, 8_001, head())},
        "SKILL.md has 8,001 bytes, over the 8,000-byte limit",
    )


def test_a_300_line_reference_under_8000_bytes_passes(tmp_path: Path) -> None:
    assert_passes(tmp_path, {"references/a.md": text_of(300, 2_000)})


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


def warns(tmp_path: Path, body: str) -> list[str]:
    """The warn lines for the good skill with `body` below its title; the skill must still pass."""
    code, fails, out = run(tmp_path, {"SKILL.md": head() + body})
    assert (code, fails) == (0, []), out
    return [line.strip() for line in out.splitlines() if line.strip().startswith("warn ")]


def as_items(count: int, marker: str = "-") -> str:
    """`count` one-sentence rules as list items marked `marker`, each wrapped onto a continuation line.

    A numbered marker ("1." or "1)") numbers the items 1, 2, 3 ... with its punctuation.
    """
    marks = [f"{i}{marker[-1]}" for i in range(1, count + 1)] if marker[:-1].isdigit() else [marker] * count
    return "".join(f"{mark} A rule is\n  one sentence.\n" for mark in marks)


def as_paragraphs(*sizes: int) -> str:
    """One-sentence rules as paragraphs of `sizes` sentences, each sentence wrapped onto two lines."""
    return "\n".join("A rule is\none sentence.\n" * size for size in sizes)


def test_41_rules_as_list_items_warn(tmp_path: Path) -> None:
    assert warns(tmp_path, as_items(41)) == [WARN_41]


def test_41_rules_as_paragraphs_warn(tmp_path: Path) -> None:
    assert warns(tmp_path, as_paragraphs(*[5] * 8, 1)) == [WARN_41]


def test_40_rules_as_list_items_do_not_warn(tmp_path: Path) -> None:
    assert warns(tmp_path, as_items(40)) == []


def test_40_rules_as_paragraphs_do_not_warn(tmp_path: Path) -> None:
    assert warns(tmp_path, as_paragraphs(*[5] * 7, 4, 1)) == []


def test_41_rules_as_plus_list_items_warn(tmp_path: Path) -> None:
    # Markdown makes a list item of "+ " as of "- ".
    assert warns(tmp_path, as_items(41, "+")) == [WARN_41]


def test_41_rules_as_list_items_numbered_with_a_parenthesis_warn(tmp_path: Path) -> None:
    # Markdown makes a numbered list item of "1) " as of "1. ".
    assert warns(tmp_path, as_items(41, "1)")) == [WARN_41]


def forty_items_and(case: str) -> str:
    """40 one-sentence list items, then `case`: the body is over 40 rules if `case` adds a rule."""
    return as_items(40) + case


def test_an_indented_paragraph_after_a_blank_line_in_an_item_is_not_a_rule(tmp_path: Path) -> None:
    assert warns(tmp_path, forty_items_and("\n  A paragraph inside the last item. It has two sentences.\n")) == []


def test_a_blockquote_is_not_a_rule(tmp_path: Path) -> None:
    assert warns(tmp_path, forty_items_and("\n> A quoted brief. It has two sentences.\n")) == []


def test_a_lazy_continuation_line_of_a_blockquote_is_not_a_rule(tmp_path: Path) -> None:
    assert warns(tmp_path, forty_items_and("\n> A quoted brief\nwraps lazily. It has two sentences.\n")) == []


def test_a_lazy_continuation_line_of_an_item_is_not_a_rule(tmp_path: Path) -> None:
    assert warns(tmp_path, forty_items_and("An unindented line of the last item. It has two sentences.\n")) == []


def test_a_thematic_break_of_dashes_is_not_a_rule(tmp_path: Path) -> None:
    assert warns(tmp_path, forty_items_and("\n---\n")) == []


def test_a_thematic_break_of_spaced_stars_is_not_a_rule(tmp_path: Path) -> None:
    assert warns(tmp_path, forty_items_and("\n* * *\n")) == []


def test_a_nested_list_item_is_a_rule(tmp_path: Path) -> None:
    assert warns(tmp_path, forty_items_and("  - A nested rule.\n")) == [WARN_41]


def test_a_table_and_a_fenced_block_are_not_rules(tmp_path: Path) -> None:
    # Each would be over 40 rules if it were counted: the table as a paragraph, the fenced block as list items.
    rows = "".join(f"| {i} | Rule {i} is one sentence. Rule {i} has a second. |\n" for i in range(1, 46))
    table = "| Rule | Text |\n| --- | --- |\n" + rows
    fenced = "```text\n" + "".join(f"- Rule {i} is one sentence.\n" for i in range(1, 46)) + "```\n"
    assert warns(tmp_path, table + "\n" + fenced) == []


def test_a_paragraph_indented_less_than_its_item_after_a_blank_line_is_a_rule(tmp_path: Path) -> None:
    # After a blank line an item holds only lines indented to its text; this one ends the list.
    assert warns(tmp_path, forty_items_and("\n A paragraph after the list.\n")) == [WARN_41]


def test_a_paragraph_after_a_list_is_a_rule(tmp_path: Path) -> None:
    assert warns(tmp_path, forty_items_and("\nA paragraph after the list.\n")) == [WARN_41]


def test_a_paragraph_after_a_blockquote_is_a_rule(tmp_path: Path) -> None:
    assert warns(tmp_path, forty_items_and("\n> A quote.\n\nA paragraph after the quote.\n")) == [WARN_41]


def test_a_setext_heading_is_not_a_rule(tmp_path: Path) -> None:
    assert warns(tmp_path, forty_items_and("\nA setext heading. It has two sentences.\n---\n")) == []


def test_indented_code_is_not_a_rule(tmp_path: Path) -> None:
    # Above the list: below it, an indented line is the last item's text.
    assert warns(tmp_path, "    Indented code. It has two sentences.\n\n" + as_items(40)) == []


def test_an_html_block_is_not_a_rule(tmp_path: Path) -> None:
    assert warns(tmp_path, forty_items_and("\n<!-- An HTML comment. It has two sentences. -->\n")) == []


def test_old_is_not_an_option(tmp_path: Path) -> None:
    code, _, out = run(tmp_path, {}, ("--old", "x"))
    assert code == 2 and "unrecognized arguments: --old" in out, out
