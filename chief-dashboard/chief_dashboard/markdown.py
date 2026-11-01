"""Markdown as the leads and the chief of staff write it in the standup room, rendered to HTML a page can hold.

Standard library only, like the rest of the page: the service runs /usr/bin/python3. It covers what a standup line
holds: fenced code blocks, inline code, bold, italics, strikethrough (`~~` only, so "~5 min" stays text), headings,
bullet and numbered lists (nested, with GitHub's task boxes), block quotes, rules, GitHub-style tables, links written
as `[text](url)` or `<url>`, and bare URLs and dispatch:// references, which become links as `render.linkify` makes
them. Raw HTML is shown as text, and an indented block is not code: a fence is the one way to write a code block.

Two things hold for any input, and they keep one line from changing how the next one renders (2026-11-01: a URL in
backticks took the closing backtick into its link, the page's `<code>` was closed inside the link's href, and every
later line in the room rendered as code):

- every character of the text is escaped, so a line never emits a tag of its own;
- every element a line opens is closed inside that line, in order. Code spans and links are cut out of the text
  before anything else reads it, and emphasis pairs its delimiters by CommonMark's stack rule, so its tags nest.
"""

from __future__ import annotations

import html
import re
import unicodedata
from functools import lru_cache

from . import render

# Quotes and lists nested deeper than this render as text, so no line can exhaust the stack.
MAX_DEPTH = 12


def e(s: str) -> str:
    return html.escape(s, quote=True)


def to_html(text: str, web: str) -> str:
    """The whole line, block by block. `web` is Dispatch's web address, for dispatch:// links."""
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return _join(_blocks(lines, web, 0), tight=False)


# ------------------------------------------------------------------------------------------------- blocks
_FENCE = re.compile(r"^( {0,3})(`{3,}|~{3,})(.*)$")
_ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
_HR = re.compile(r"^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$")
_QUOTE = re.compile(r"^ *> ?(.*)$")
_BULLET = re.compile(r"^( *)([-+*])(?:([ \t]+)(.*)|$)")
_ORDERED = re.compile(r"^( *)(\d{1,9})([.)])(?:([ \t]+)(.*)|$)")
_DELIM_ROW = re.compile(r"^ *\|?[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)*\|?[ \t]*$")
_TASK = re.compile(r"^\[([ xX])\][ \t]+")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _fence(line: str):
    m = _FENCE.match(line)
    if m and not (m.group(2)[0] == "`" and "`" in m.group(3)):  # "```a``` b" is inline code, not a fence
        return m
    return None


class _Item:
    """A list item's first line: its marker and where its content starts."""

    def __init__(self, line: str):
        self.ok = False
        if m := _BULLET.match(line):
            self.ordered, self.mark, self.start = False, "", 1
            indent, marker, space, rest = len(m.group(1)), m.group(2), m.group(3) or "", m.group(4) or ""
        elif m := _ORDERED.match(line):
            self.ordered, self.mark, self.start = True, m.group(3), int(m.group(2))
            indent, marker, space, rest = len(m.group(1)), m.group(2) + m.group(3), m.group(4) or "", m.group(5) or ""
        else:
            return
        self.ok, self.indent, self.rest = True, indent, rest
        # Content starts after the marker and its spaces; past four spaces, one of them is the separator.
        self.offset = indent + len(marker) + (len(space) if 0 < len(space) <= 4 else 1)

    def sibling(self, other: _Item) -> bool:
        return other.ok and other.ordered == self.ordered and other.mark == self.mark


def _interrupts(line: str) -> bool:
    """Does this line start a block that ends a paragraph? A numbered list does only when it starts at 1."""
    if _fence(line) or _ATX.match(line) or _HR.match(line) or _QUOTE.match(line):
        return True
    item = _Item(line)
    return item.ok and bool(item.rest.strip()) and (not item.ordered or item.start == 1)


def _cells(line: str) -> list[str]:
    """A table row's cells: split on each pipe that is not escaped as `\\|`."""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    cells, cur, i = [], [], 0
    while i < len(s):
        if s[i] == "\\" and s[i + 1 : i + 2] == "|":
            cur.append("|")
            i += 2
            continue
        if s[i] == "|":
            cells.append("".join(cur).strip())
            cur = []
        else:
            cur.append(s[i])
        i += 1
    cells.append("".join(cur).strip())
    return cells


def _table_at(lines: list[str], i: int) -> list[str | None] | None:
    """The column alignments when a table starts at line i: a header row, then a delimiter row with as many cells."""
    if i + 1 >= len(lines) or "|" not in lines[i] or "|" not in lines[i + 1] or not _DELIM_ROW.match(lines[i + 1]):
        return None
    head, delim = _cells(lines[i]), _cells(lines[i + 1])
    if len(head) != len(delim):
        return None
    aligns: list[str | None] = []
    for d in delim:
        left, right = d.startswith(":"), d.endswith(":")
        aligns.append("center" if left and right else "right" if right else "left" if left else None)
    return aligns


def _table(lines: list[str], i: int, aligns: list[str | None], web: str) -> tuple[str, int]:
    styles = [f' style="text-align:{a}"' if a else "" for a in aligns]

    def row(line: str, tag: str) -> str:
        cells = (_cells(line) + [""] * len(aligns))[: len(aligns)]  # a short row is padded, a long one cut
        return "<tr>" + "".join(
            f"<{tag}{s}>{inline(c, web)}</{tag}>" for c, s in zip(cells, styles, strict=True)
        ) + "</tr>"

    head = row(lines[i], "th")
    i += 2
    body = []
    while i < len(lines) and lines[i].strip() and not _interrupts(lines[i]):
        body.append(row(lines[i], "td"))
        i += 1
    tbody = f"<tbody>{''.join(body)}</tbody>" if body else ""
    return f"<table><thead>{head}</thead>{tbody}</table>", i


def _blocks(lines: list[str], web: str, depth: int) -> list[tuple[str, str]]:
    """The blocks of `lines`, each as ("p", inline HTML) for a paragraph or ("block", HTML) for anything else."""
    if depth > MAX_DEPTH:
        text = "\n".join(ln.strip() for ln in lines if ln.strip())
        return [("p", inline(text, web))] if text else []
    out: list[tuple[str, str]] = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if m := _fence(line):
            indent, fence, info = len(m.group(1)), m.group(2), m.group(3).strip()
            closing = re.compile(rf"^ {{0,3}}{re.escape(fence[0])}{{{len(fence)},}}[ \t]*$")
            body = []
            i += 1
            while i < n and not closing.match(lines[i]):  # a fence its writer never closed runs to the line's end
                body.append(lines[i][min(indent, _indent(lines[i])) :])
                i += 1
            i += 1
            lang = info.split()[0] if info else ""
            cls = f' class="language-{e(lang)}"' if re.fullmatch(r"[A-Za-z0-9_+.#-]{1,40}", lang) else ""
            code = render.linkify("\n".join(body), web)
            out.append(("block", f"<pre><code{cls}>{code}</code></pre>"))
            continue
        if _HR.match(line):
            out.append(("block", "<hr>"))
            i += 1
            continue
        if m := _ATX.match(line):
            level = len(m.group(1))
            out.append(("block", f"<h{level}>{inline((m.group(2) or '').strip(), web)}</h{level}>"))
            i += 1
            continue
        if _QUOTE.match(line):
            inner = []
            while i < n and lines[i].strip():
                if q := _QUOTE.match(lines[i]):
                    inner.append(q.group(1))
                elif inner[-1].strip() and not _interrupts(lines[i]):  # a lazy line continues the quote's paragraph
                    inner.append(lines[i])
                else:
                    break
                i += 1
            out.append(("block", f"<blockquote>{_join(_blocks(inner, web, depth + 1), tight=False)}</blockquote>"))
            continue
        if _Item(line).ok:
            html_list, i = _list(lines, i, web, depth)
            out.append(("block", html_list))
            continue
        if (aligns := _table_at(lines, i)) is not None:
            html_table, i = _table(lines, i, aligns, web)
            out.append(("block", html_table))
            continue
        para = [line.strip()]
        i += 1
        while i < n and lines[i].strip() and not _interrupts(lines[i]) and _table_at(lines, i) is None:
            para.append(lines[i].strip())
            i += 1
        out.append(("p", inline("\n".join(para), web)))
    return out


def _list(lines: list[str], i: int, web: str, depth: int) -> tuple[str, int]:
    first = _Item(lines[i])
    n = len(lines)
    items: list[list[str]] = []
    loose = False
    while i < n:
        item = _Item(lines[i])
        if not first.sibling(item):
            break
        # A line indented two columns past the marker belongs to the item, as does one at its content's column.
        threshold = min(item.offset, item.indent + 2)
        body = [item.rest]
        i += 1
        while i < n:
            line = lines[i]
            if not line.strip():
                j = i
                while j < n and not lines[j].strip():
                    j += 1
                if j < n and _indent(lines[j]) >= threshold:
                    body.extend([""] * (j - i))
                    i = j
                    continue
                break
            ind = _indent(line)
            if ind >= threshold:
                body.append(line[min(ind, item.offset) :])
            elif body[-1].strip() and not _interrupts(line) and not _Item(line).ok:
                body.append(line.strip())  # a lazy line continues the item's paragraph
            else:
                break
            i += 1
        while body and not body[-1].strip():
            body.pop()
        if "" in body:
            loose = True
        items.append(body)
        if i < n and not lines[i].strip():
            j = i
            while j < n and not lines[j].strip():
                j += 1
            if j < n and first.sibling(_Item(lines[j])):
                loose = True
                i = j
                continue
            break
    parts = []
    for body in items:
        box = ""
        if body and (t := _TASK.match(body[0])):
            box = '<input type="checkbox" disabled checked> ' if t.group(1) != " " else '<input type="checkbox" disabled> '
            body = [body[0][t.end() :], *body[1:]]
        parts.append(f"<li>{box}{_join(_blocks(body, web, depth + 1), tight=not loose)}</li>")
    if first.ordered:
        start = f' start="{first.start}"' if first.start != 1 else ""
        return f"<ol{start}>{''.join(parts)}</ol>", i
    return f"<ul>{''.join(parts)}</ul>", i


def _join(blocks: list[tuple[str, str]], tight: bool) -> str:
    """Blocks in order. In a tight list item a paragraph is its text alone, as GitHub renders it."""
    return "".join(h if kind != "p" or tight else f"<p>{h}</p>" for kind, h in blocks)


# ------------------------------------------------------------------------------------------------- inline
_ASCII_PUNCT = frozenset("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")
_SPECIAL = re.compile(r"\\[\s\S]|`+|\[|<|\*+|_+|~+|" + render._LINK.pattern)
_MDLINK = re.compile(
    r"\[((?:[^\[\]\\\n]|\\.)*)\]\([ \t]*<?((?:https?|dispatch)://[^\s()<>]+)>?(?:[ \t]+\"[^\"\n]*\")?[ \t]*\)"
)
_AUTOLINK = re.compile(r"<((?:https?|dispatch)://[^\s<>]+)>")
_TRAILING = "?!.,:*_~"  # GitHub leaves these out of the end of a bare link


@lru_cache(maxsize=64)
def _ticks(n: int) -> re.Pattern:
    return re.compile(rf"(?<!`)`{{{n}}}(?!`)")


def _punct(c: str) -> bool:
    return c in _ASCII_PUNCT or unicodedata.category(c)[0] in "PS"


class _Run:
    """A run of `*`, `_` or `~`: maybe emphasis, decided when the line's runs are paired."""

    __slots__ = ("char", "count", "orig", "can_open", "can_close", "seq", "opens", "closes")

    def __init__(self, text: str, start: int, end: int, seq: int):
        self.char, self.count, self.orig, self.seq = text[start], end - start, end - start, seq
        before = text[start - 1] if start > 0 else "\n"
        after = text[end] if end < len(text) else "\n"
        sp_b, sp_a, pu_b, pu_a = before.isspace(), after.isspace(), _punct(before), _punct(after)
        left = not sp_a and (not pu_a or sp_b or pu_b)
        right = not sp_b and (not pu_b or sp_a or pu_a)
        if self.char == "_":  # an underscore inside a word (snake_case) is never emphasis
            self.can_open, self.can_close = left and (not right or pu_b), right and (not left or pu_a)
        else:
            self.can_open, self.can_close = left, right
        self.opens: list[str] = []
        self.closes: list[str] = []

    def html(self) -> str:
        return "".join(self.closes) + e(self.char * self.count) + "".join(self.opens)


def _pairs(op: _Run, closer: _Run) -> bool:
    if closer.char == "~":
        return op.orig == closer.orig == 2 and op.count == closer.count == 2
    # CommonMark's rule of three, so "*a**b*" pairs the way GitHub pairs it.
    both = op.can_close or closer.can_open
    return not (both and (op.orig + closer.orig) % 3 == 0 and not (op.orig % 3 == 0 and closer.orig % 3 == 0))


def _emphasis(runs: list[_Run]) -> None:
    """Pair openers with closers (CommonMark's "process emphasis"). Every pair encloses only whole pairs, so the
    tags it adds always nest."""
    stack = list(runs)
    floor: dict[tuple, int] = {}  # the lowest run worth searching for an opener, per kind of closer
    ci = 0
    while ci < len(stack):
        closer = stack[ci]
        if not closer.can_close or not closer.count:
            ci += 1
            continue
        key = (closer.char, closer.can_open, closer.orig % 3)
        bottom, found = floor.get(key, -1), None
        oi = ci - 1
        while oi >= 0 and stack[oi].seq > bottom:
            op = stack[oi]
            if op.char == closer.char and op.can_open and op.count and _pairs(op, closer):
                found = oi
                break
            oi -= 1
        if found is None:
            floor[key] = stack[ci - 1].seq if ci > 0 else -1
            if closer.can_open:
                ci += 1
            else:
                del stack[ci]
            continue
        op = stack[found]
        use = 2 if closer.char == "~" or (op.count >= 2 and closer.count >= 2) else 1
        tag = "del" if closer.char == "~" else "strong" if use == 2 else "em"
        op.count -= use
        closer.count -= use
        op.opens.insert(0, f"<{tag}>")
        closer.closes.append(f"</{tag}>")
        del stack[found + 1 : ci]
        ci = found + 1
        if not op.count:
            del stack[found]
            ci -= 1
        if not closer.count:
            del stack[ci]


def _anchor(url: str, inner: str, web: str) -> str:
    href = url if url.startswith("http") else render._dispatch_url(url, web)
    return f'<a href="{e(href)}" target="_blank" rel="noopener">{inner}</a>'


def _code(content: str, web: str, links: bool) -> str:
    content = content.replace("\n", " ")
    if len(content) > 1 and content[0] == content[-1] == " " and content.strip():
        content = content[1:-1]
    return render.linkify(content, web) if links else e(content)


def inline(text: str, web: str, links: bool = True) -> str:
    """One block's text: code spans, links and emphasis; the rest escaped, its line breaks kept as <br>.

    `links` is False inside a link's own text, which cannot hold another link."""
    nodes: list[str | _Run] = []
    runs: list[_Run] = []
    buf: list[str] = []
    no_closer: dict[int, int] = {}  # backtick run length -> the position past which none of that length exists

    def flush() -> None:
        if buf:
            nodes.append(e("".join(buf)).replace("\n", "<br>"))
            buf.clear()

    pos, n = 0, len(text)
    while pos < n:
        m = _SPECIAL.search(text, pos)
        if m is None:
            buf.append(text[pos:])
            break
        start, end = m.span()
        buf.append(text[pos:start])
        tok, c = m.group(0), m.group(0)[0]
        pos = end
        if c == "\\":
            if tok[1] in _ASCII_PUNCT or tok[1] == "\n":
                buf.append(tok[1])
            else:
                buf.append("\\")
                pos = start + 1
        elif c == "`":
            k = len(tok)
            close = None if no_closer.get(k, n + 1) <= end else _ticks(k).search(text, end)
            if close is None:
                no_closer[k] = min(no_closer.get(k, n + 1), end)
                buf.append(tok)  # a backtick nothing closes is a backtick
            else:
                flush()
                nodes.append(f"<code>{_code(text[end : close.start()], web, links)}</code>")
                pos = close.end()
        elif c == "[" and links and (lk := _MDLINK.match(text, start)):
            flush()
            nodes.append(_anchor(lk.group(2), inline(lk.group(1), web, links=False), web))
            pos = lk.end()
        elif c == "<" and links and (lk := _AUTOLINK.match(text, start)):
            flush()
            nodes.append(_anchor(lk.group(1), e(lk.group(1)), web))
            pos = lk.end()
        elif c in "*_~":
            flush()
            run = _Run(text, start, end, len(runs))
            runs.append(run)
            nodes.append(run)
        elif c in "[<":
            buf.append(c)
        else:  # a bare URL or dispatch:// reference
            ref = tok.rstrip(_TRAILING)
            pos = start + len(ref)
            if links:
                flush()
                nodes.append(render.linkify(ref, web))
            else:
                buf.append(ref)
    flush()
    _emphasis(runs)
    return "".join(node.html() if isinstance(node, _Run) else node for node in nodes)
