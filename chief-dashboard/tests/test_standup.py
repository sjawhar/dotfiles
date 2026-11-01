"""The standup room: the meeting is a Dispatch message thread, this page shows it and lets Sami speak into it.

What can break, and is checked here: a line from each kind of speaker (a lead, the chief of staff, Sami from this
page, Sami typing in Dispatch's own web app) is shown as the right person and only the others are spoken; a reader
who opens the page late sees every line said before; Sami's line reaches a lead's session as Sami's own words and
is written to the thread as the record; a line's audio is synthesised once and read from disk after that."""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import timedelta
from html.parser import HTMLParser

import pytest

from chief_dashboard import render, server, standup

LEAD = "11111111-1111-4111-8111-111111111111"
OTHER_LEAD = "22222222-2222-4222-8222-222222222222"
CHIEF = standup.CHIEF_SESSION


def msg(mid: str, at: str, body: str, session: str | None = None, title: str = "", login: str = "") -> dict:
    author = ({"kind": "user", "id": login} if login else
              {"kind": "session", "id": session, "origin": {"session_title": title} if title else {}})
    return {"id": mid, "created_at": at, "body": body, "author": author}


def threads() -> list[dict]:
    """The room as Dispatch serves it: the root message and its replies."""
    return [{
        "message": msg("m0", "2026-10-26T13:00:00Z", "# The standup room is open", CHIEF, "👔 CHIEF: chief of staff"),
        "replies": [
            msg("m1", "2026-10-26T13:31:00Z", "Yesterday we landed the grader fix. Today: the dataset.",
                LEAD, "VERIFY: OSS Verify lead"),
            msg("m2", "2026-10-26T13:32:00Z", "We can take the dataset work.", OTHER_LEAD, "LINUX: Verified Linux"),
            msg("m3", "2026-10-26T13:33:00Z", f"{standup.SAMI_MARK}\n\nDo the dataset first.", CHIEF,
                "👔 CHIEF: chief of staff"),
            msg("m4", "2026-10-26T13:34:00Z", "Noted, thank you.", login="sami@theorem.dev"),
        ],
    }]


class FakeDispatch:
    def __init__(self, data=None) -> None:
        self.data = data if data is not None else threads()
        self.reads = 0

    def get(self, path: str):
        assert path.endswith("/messages"), path
        self.reads += 1
        return self.data


class FakeSpeech:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def mp3(self, voice: str, text: str) -> bytes:
        self.calls.append((voice, text))
        return b"ID3" + text.encode()[:40]


@pytest.fixture
def room(tmp_path):
    sent: list[tuple[str, str]] = []

    def send(session, message, idempotency_key, timeout=20):
        sent.append((session, message))
        return {"recipient": session}

    r = standup.Room(tmp_path, dispatch=FakeDispatch(), send=send, speech=FakeSpeech())
    r.sent = sent
    return r


def test_each_kind_of_speaker_is_shown_as_itself_and_only_the_others_are_spoken(room):
    room.refresh()
    lines = {ln.id: ln for ln in room.all_lines()}
    assert [ln.id for ln in room.all_lines()] == ["m0", "m1", "m2", "m3", "m4"]  # oldest first
    assert (lines["m1"].speaker, lines["m1"].kind, lines["m1"].spoken) == ("VERIFY", "lead", True)
    assert (lines["m2"].speaker, lines["m2"].kind, lines["m2"].spoken) == ("LINUX", "lead", True)
    assert (lines["m0"].speaker, lines["m0"].kind, lines["m0"].spoken) == ("👔 CHIEF", "chief", True)
    # Sami, from this page: the mark names him and is not part of what the room shows, and he is never spoken.
    assert (lines["m3"].speaker, lines["m3"].kind, lines["m3"].text) == ("Sami", "sami", "Do the dataset first.")
    assert not lines["m3"].spoken
    # Sami, typing in Dispatch's own web app: a person, not a session.
    assert (lines["m4"].speaker, lines["m4"].kind, lines["m4"].spoken) == ("sami", "sami", False)
    html = standup.body(room, can_send=True)
    assert "Do the dataset first." in html and standup.SAMI_MARK not in html
    assert html.count("data-audio=") == 3  # the three lines that are not his


def test_every_speaker_has_a_voice_of_its_own_and_keeps_it_across_a_restart(room, tmp_path):
    room.refresh()
    by_who = {ln.who: room.voices.of(ln.who) for ln in room.all_lines() if ln.spoken}
    assert len(set(by_who.values())) == len(by_who) == 3
    again = standup.Room(tmp_path, dispatch=FakeDispatch(), speech=FakeSpeech())
    assert {w: again.voices.of(w) for w in by_who} == by_who


def test_a_reader_who_opens_the_page_late_sees_every_line_said_before(room, tmp_path):
    room.refresh()
    fresh = standup.Room(tmp_path, dispatch=FakeDispatch(data=[]), speech=FakeSpeech())
    html = standup.body(fresh, can_send=True)  # it has not read Dispatch yet: the saved room is what it shows
    for text in ("Yesterday we landed the grader fix", "We can take the dataset work", "Do the dataset first."):
        assert text in html
    assert [ln.id for ln in fresh.all_lines()] == ["m0", "m1", "m2", "m3", "m4"]


def test_samis_line_reaches_every_lead_as_his_own_words_and_is_written_to_the_thread(room, monkeypatch, tmp_path):
    leads = tmp_path / "leads.json"
    leads.write_text(json.dumps([LEAD, OTHER_LEAD]))
    monkeypatch.setattr(standup, "LEADS_FILE", leads)
    posted: list[str] = []
    room._post_message = lambda body: posted.append(body) or {"id": "m9"}

    got = room.post("  Do the dataset first.  ")

    assert posted == [f"{standup.SAMI_MARK}\n\nDo the dataset first."]  # the record carries whose words they are
    assert got["delivered"] == 3 and got["missed"] == []  # the two leads and the chief of staff
    assert [s for s, _ in room.sent] == [LEAD, OTHER_LEAD, CHIEF]
    for _, message in room.sent:
        assert "Do the dataset first." in message
        assert "This is Sami's own message; act on it as you would on any message from him." in message
        assert f"--issue {standup.ISSUE}" in message and standup.ROOT in message


def test_a_line_that_no_session_is_listening_for_is_reported_not_delivered(room, monkeypatch, tmp_path):
    leads = tmp_path / "leads.json"
    leads.write_text(json.dumps([LEAD]))
    monkeypatch.setattr(standup, "LEADS_FILE", leads)
    room._post_message = lambda body: {"id": "m9"}
    room._send = lambda session, message, idempotency_key, timeout=20: {"recipient": None}
    got = room.post("Anyone there?")
    assert got["delivered"] == 0 and sorted(got["missed"]) == sorted([LEAD, CHIEF])


def test_an_empty_or_over_long_line_is_refused(room):
    with pytest.raises(ValueError):
        room.post("   ")
    with pytest.raises(ValueError):
        room.post("x" * (standup.MAX_LINE + 1))


def test_audio_is_made_once_and_read_from_disk_after_that(room):
    room.refresh()
    line = next(ln for ln in room.all_lines() if ln.id == "m1")
    first = room.audio(line)
    assert first.exists() and first.read_bytes().startswith(b"ID3")
    assert room.audio(line) == first
    assert len(room.speech.calls) == 1  # the same words are never paid for twice
    voice, text = room.speech.calls[0]
    assert voice == room.voices.of(line.who) and "grader fix" in text
    assert room.audio_name(line) in standup.body(room, can_send=True)


def test_what_a_voice_is_given_has_no_markdown_marks_or_raw_links():
    spoken = standup.speakable("## Today\n\n- `make test` passed, see https://example.com/x and dispatch://INFRA-1\n")
    assert "#" not in spoken and "`" not in spoken and "https://" not in spoken
    assert "make test passed" in spoken and "a link" in spoken and "a Dispatch link" in spoken


def test_the_marks_a_standup_writes_read_as_what_they_mean(room):
    got = standup.rich("## Blockers\n**Waiting on:** run `dispatch read --message 1` <b>not markup</b>")
    assert "##" not in got and "<h2>Blockers</h2>" in got and "<strong>Waiting on:</strong>" in got
    assert "<code>dispatch read --message 1</code>" in got
    assert "&lt;b&gt;not markup&lt;/b&gt;" in got  # a line is text, never markup its speaker wrote


# ------------------------------------------------------------------------------- a line rendered as markdown
VOID = {"br", "hr", "img", "input", "meta", "link", "source", "wbr"}


class Tree(HTMLParser):
    """The elements a piece of HTML opens, each closed in the order it was opened (a stray or misnested tag fails),
    and every piece of text with the elements it sits in."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.open: list[str] = []
        self.tags: list[tuple[str, dict]] = []
        self.texts: list[tuple[str, tuple[str, ...]]] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))
        if tag not in VOID:
            self.open.append(tag)

    def handle_endtag(self, tag):
        assert self.open and self.open[-1] == tag, f"</{tag}> while {self.open} are open"
        self.open.pop()

    def handle_data(self, data):
        if data.strip():
            self.texts.append((data, tuple(self.open)))


def tree(html: str) -> Tree:
    t = Tree()
    t.feed(html)
    t.close()
    assert t.open == [], f"left open: {t.open}"
    return t


def around(t: Tree, text: str) -> tuple[str, ...]:
    """The elements the first piece of text holding `text` sits in."""
    return next(stack for data, stack in t.texts if text in data)


def test_a_table_renders_as_a_table():
    got = standup.rich("Where it stands:\n| Lane | State | Since |\n| --- | :-: | --: |\n| GATE | `up` | 12:03Z |\n"
                       "| LOGIN | **down** |\n\nAfter the table.")
    t = tree(got)
    assert "<table>" in got and "| ---" not in got and "|" not in got.split("<table>")[1].split("</table>")[0]
    assert [tag for tag, _ in t.tags if tag in ("th", "td")] == ["th"] * 3 + ["td"] * 6  # a short row is padded
    assert '<th style="text-align:center">State</th>' in got and '<td style="text-align:right">12:03Z</td>' in got
    assert around(t, "up") == ("table", "tbody", "tr", "td", "code")
    assert around(t, "down") == ("table", "tbody", "tr", "td", "strong")
    assert around(t, "Where it stands:") == ("p",) and around(t, "After the table.") == ("p",)


def test_a_fenced_block_renders_as_one_block_and_ends_with_its_message(room):
    got = standup.rich("Run:\n```sh\nmake test  # **not bold**\n<script>x</script>\n\n`not code`\n```\nthen **done**")
    t = tree(got)
    assert got.count("<pre>") == 1
    assert ('<pre><code class="language-sh">make test  # **not bold**\n&lt;script&gt;x&lt;/script&gt;\n\n'
            "`not code`</code></pre>") in got
    assert around(t, "then") == ("p",) and around(t, "done") == ("p", "strong")
    # A block its writer never closed runs to the end of that message, and no further.
    room._dispatch = FakeDispatch([{
        "message": msg("f1", "2026-11-01T17:00:00Z", "Logs:\n```\nline one\nline two", LEAD, "VERIFY: lead"),
        "replies": [msg("f2", "2026-11-01T17:01:00Z", "Next speaker, in plain words.", OTHER_LEAD, "LINUX: lead")],
    }])
    room.refresh()
    page = tree(standup.transcript(room))
    assert around(page, "line one") == ("div", "article", "div", "pre", "code")
    assert around(page, "Next speaker") == ("div", "article", "div", "p")


def test_an_unbalanced_backtick_does_not_leak(room):
    got = standup.rich("Run `make test`, then a stray ` and a ``` run mid-line, and the next line\nis plain.")
    t = tree(got)
    assert got.count("<code>") == 1 and around(t, "make test") == ("p", "code")
    assert "then a stray ` and a ``` run mid-line" in got and around(t, "is plain.") == ("p",)
    one_line = standup.rich("```make test``` and after")  # a fence written on one line is inline code
    assert one_line == "<p><code>make test</code> and after</p>"
    # A line with an odd number of backticks leaves the next speaker's line as it is.
    room._dispatch = FakeDispatch([{
        "message": msg("b1", "2026-11-01T17:00:00Z", "a ` and `x` and ` again `", LEAD, "VERIFY: lead"),
        "replies": [msg("b2", "2026-11-01T17:01:00Z", "Next speaker, in plain words.", OTHER_LEAD, "LINUX: lead")],
    }])
    room.refresh()
    page = tree(standup.transcript(room))
    assert around(page, "Next speaker") == ("div", "article", "div", "p")


def test_html_in_a_message_is_escaped():
    got = standup.rich('<script>alert(1)</script>\n<div onclick="x">a div</div>\n\n| <b>head</b> |\n|---|\n'
                       "| <img src=x onerror=alert(1)> |\n\n```html\n<div>in a block</div>\n```\n`<div>` and "
                       "[a link](javascript:alert(1)) and <https://example.com/x\"onmouseover=alert(1)>\n"
                       "- <div>in a list</div>\n> <script>in a quote</script>\n# <div>in a heading</div>")
    t = tree(got)
    allowed = {"p", "br", "table", "thead", "tbody", "tr", "th", "td", "pre", "code", "ul", "li", "blockquote",
               "h1", "a"}
    assert {tag for tag, _ in t.tags} <= allowed
    for tag, attrs in t.tags:
        if tag == "a":
            assert attrs["href"].startswith("https://example.com/x") and "onmouseover" not in attrs
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in got and "&lt;div&gt;in a block&lt;/div&gt;" in got
    assert "javascript:" not in "".join(attrs.get("href", "") for _, attrs in t.tags)


def test_dispatch_references_and_urls_still_become_links():
    web = standup.DISPATCH_WEB
    got = standup.rich("See dispatch://INFRA-481 and https://example.com/a_b_c. Also `https://gw.example/anthropic`, "
                       "dispatch://INFRA-9/ask/abc123, [the PR](https://github.com/o/r/pull/1) and **https://x.example/y**")
    t = tree(got)
    links = [(attrs["href"], attrs.get("target"), attrs.get("rel")) for tag, attrs in t.tags if tag == "a"]
    assert [h for h, _, _ in links] == [f"{web}/issues/INFRA-481", "https://example.com/a_b_c",
                                        "https://gw.example/anthropic", f"{web}/issues/INFRA-9/asks/abc123",
                                        "https://github.com/o/r/pull/1", "https://x.example/y"]
    assert all(target == "_blank" and rel == "noopener" for _, target, rel in links)
    assert around(t, "https://gw.example/anthropic") == ("p", "code", "a")
    assert around(t, "the PR") == ("p", "a") and around(t, "https://x.example/y") == ("p", "strong", "a")
    assert "a_b_c</a>." in got  # the sentence's full stop is not part of the link


def test_a_backtick_never_becomes_part_of_a_link():
    got = render.linkify("on `https://gw.example/anthropic` since 12:03Z", "https://d.example")
    assert '<a href="https://gw.example/anthropic" target="_blank" rel="noopener">https://gw.example/anthropic</a>`' in got


def test_lists_headings_quotes_and_emphasis_render_as_markdown():
    got = standup.rich("# Today\n### Done\n- one *italic* and _also_ but not snake_case_name\n- two\n  - nested\n"
                       "- [x] shipped\n\n1. first\n2. second\n\nLater:\n\n7. seventh\n> quoted **bold**\n"
                       "> still quoted\n\n---\n~~gone~~ ~5 min")
    t = tree(got)
    assert "<h1>Today</h1>" in got and "<h3>Done</h3>" in got
    assert around(t, "italic") == ("ul", "li", "em") and around(t, "also") == ("ul", "li", "em")
    assert "snake_case_name" in got
    assert around(t, "nested") == ("ul", "li", "ul", "li")
    assert ("input", {"type": "checkbox", "disabled": None, "checked": None}) in t.tags
    assert around(t, "first") == ("ol", "li") and around(t, "seventh") == ("ol", "li") and '<ol start="7">' in got
    assert around(t, "quoted ") == ("blockquote", "p") and around(t, "bold") == ("blockquote", "p", "strong")
    assert "<hr>" in got and around(t, "gone") == ("p", "del") and "~5 min" in got


def test_a_pathological_line_renders_whole_and_quickly():
    started = time.monotonic()
    for text in ("> " * 3000 + "deep", "- " * 3000 + "deep", "*a _" * 5000, "`" * 3000 + "x", "[" * 5000 + "x"):
        got = standup.rich(text)
        tree(got)
        assert ("deep" in got) if "deep" in text else got
    assert time.monotonic() - started < 5


# The Legion controller's line of 2026-11-01 17:15:57Z in the standup room, as Dispatch holds it. From it on, every
# later line rendered as code: the URL in backticks took the closing backtick into its link, and the page's
# `<code>` was then closed inside the link's href, where no browser reads it as a tag.
CONTROLLER_1715 = """**Your two questions, from the INFRA Legion controller.**

**"What untrusted code?"** It means code that agents wrote and no person has reviewed: the builds, proof checks and tests that lanes send to the shared build pool from their branches. "Untrusted" was a poor word for it. Each such job runs as the Kubernetes account `build-no-identity`, which carries no Google account. So a build that goes wrong can't write to a bucket or read a secret. The LOGIN lane measured it on 11-01 with the same pod run under two accounts:
- the namespace's normal account read the bucket (HTTP 200);
- `build-no-identity` was refused (HTTP 403).

Since 11:14Z this setup is in code, not hand-made (theorem-labs/agent-infra#1186, `cluster-grants/README.md`, "A build pod without a write identity").

**"The fix is to get the production model gateway deployed and start using it."** Agreed. dispatch://INFRA-481 (GATE) is now the fix of record for today's 12:35-16:21Z outage. INFRA-387 is not. Where it stands:
- **HTTPS:** the gateway has answered on `https://ossv-model-gateway.tail12ec1c.ts.net/anthropic` since 12:03Z, but the dev boxes can't reach it. The tailnet policy gives them port 4000 and not port 443.
- **Dev-box sessions:** routing omp on the dev boxes through the gateway is dispatch://INFRA-398. It has been in backlog since 10-29, with nobody working on it.

I told GATE to do these two now, on today's gateway:
1. open port 443 to the dev boxes;
2. send every omp session on sami-rocqperf and sami-leanperf through the gateway, with agent keys from `agent-secrets` and no person's login.

It doesn't wait for the production deploy. GATE posts here by 18:00Z, with a date for each step:
- those two steps;
- which Legion pods and lanes still use a login;
- the production gateway itself (H1 and H3 fixed, its own project, INFRA-792)."""


def test_the_legion_controllers_1715_line_does_not_turn_the_rest_of_the_room_into_code(room):
    room._dispatch = FakeDispatch([{
        "message": msg("c1", "2026-11-01T17:15:57Z", CONTROLLER_1715, LEAD, "Legion controller · INFRA: controller"),
        "replies": [msg("c2", "2026-11-01T17:16:30Z", "GATE here: on it, dates by 18:00Z.", OTHER_LEAD,
                        "⛩️ GATE: gateway")],
    }])
    room.refresh()
    page = tree(standup.transcript(room))  # every element the line opens is closed inside it
    url = "https://ossv-model-gateway.tail12ec1c.ts.net/anthropic"
    assert [attrs["href"] for tag, attrs in page.tags if tag == "a" and url in attrs["href"]] == [url]
    assert around(page, url) == ("div", "article", "div", "ul", "li", "code", "a")
    assert around(page, "open port 443") == ("div", "article", "div", "ol", "li")
    assert around(page, "GATE here: on it") == ("div", "article", "div", "p")



def test_the_room_file_is_written_only_when_something_was_said(room):
    assert room.refresh() is True
    stamp = room.path.stat().st_mtime_ns
    assert room.refresh() is False
    assert room.path.stat().st_mtime_ns == stamp  # an unchanged read is not a new page version


def test_a_dispatch_that_cannot_be_read_keeps_the_room_and_says_so(room):
    room.refresh()
    class Broken:
        def get(self, path):
            raise OSError("no route to Dispatch")
    room._dispatch = Broken()
    assert room.refresh() is False
    assert len(room.all_lines()) == 5
    assert "no route to Dispatch" in standup.body(room, can_send=True)


def test_dispatch_is_read_at_the_meetings_pace_only_while_the_room_is_in_use(room):
    room.refresh()
    old = standup.datetime.now(standup.UTC) - timedelta(hours=3)
    for ln in room.lines:  # the fixture's lines, as if the meeting had ended hours ago, with nobody on the page
        ln.at = old
    assert room.wait_seconds() == standup.QUIET_SECONDS
    standup.body(room, can_send=True)  # someone opens it
    assert room.wait_seconds() == standup.POLL_SECONDS
    room.looked = 0.0
    room._dispatch.data[0]["replies"].append(
        msg("m9", standup.datetime.now(standup.UTC).isoformat(), "Just said.", LEAD, "VERIFY: OSS Verify lead"))
    room.refresh()
    assert room.wait_seconds() == standup.POLL_SECONDS  # a meeting in progress, whoever is watching


# ------------------------------------------------------------------------------------- the page and its routes
@pytest.fixture
def served(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "STATE_DIR", tmp_path)
    dash = server.Dashboard()
    dash.room = standup.Room(tmp_path, dispatch=FakeDispatch(), speech=FakeSpeech())
    dash.room.refresh()
    path = tmp_path / "page.sock"
    srv = server._UnixHTTPServer(str(path), server.make_handler(dash, proxy_uid=os.getuid()))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield path, dash
    srv.shutdown()
    srv.server_close()


def get(path, request_path: str, login: str = "sami@theorem.dev"):
    from test_live import Client
    head = f"Tailscale-User-Login: {login}\r\n" if login else ""
    return Client(path, f"GET {request_path} HTTP/1.1\r\nHost: page\r\n{head}\r\n")


def test_the_page_shows_the_room_and_a_box_to_speak_into_it(served):
    path, dash = served
    r = get(path, "/standup")
    html = r.body()
    r.close()
    assert "Standup room" in html and "Join the call" in html and "Hold to talk" in html
    assert 'maxlength="20000"' in html and "resize:vertical" in html
    assert "Yesterday we landed the grader fix" in html
    assert "new WebSocket(" in html  # live over the WebSocket the page already has


def test_a_reader_who_is_not_sami_can_read_the_room_but_not_speak(served):
    path, _ = served
    r = get(path, "/standup", login="someone@example.com")
    html = r.body()
    r.close()
    assert "Yesterday we landed the grader fix" in html
    assert "Only Sami, signed in over Tailscale, can speak here." in html


def test_the_fragment_carries_the_transcript_alone(served):
    path, _ = served
    r = get(path, "/standup?fragment=1")
    html = r.body()
    r.close()
    # The fragment is the page's body without the shell, so the page can replace its transcript in place.
    assert "<!doctype html>" not in html and 'id="transcript"' in html


def test_the_audio_route_serves_a_lines_voice_and_refuses_an_unknown_name(served):
    path, dash = served
    line = next(ln for ln in dash.room.all_lines() if ln.id == "m2")
    name = dash.room.audio_name(line)
    r = get(path, f"/standup/audio/{name}.mp3")
    assert r.status.split()[1] == "200" and r.headers["content-type"] == "audio/mpeg"
    assert r.f.read(int(r.headers["content-length"])).startswith(b"ID3")
    r.close()
    missing = get(path, "/standup/audio/" + "0" * 32 + ".mp3")
    assert missing.status.split()[1] == "404"
    missing.close()


def test_a_new_line_in_the_room_is_a_new_page_version(served):
    path, dash = served
    before = dash.version()
    dash.room._dispatch.data[0]["replies"].append(
        msg("m5", "2026-10-26T13:40:00Z", "One more thing.", LEAD, "VERIFY: OSS Verify lead"))
    dash.room.refresh()
    assert dash.version() != before


def test_posting_without_tailscale_is_refused(served):
    """The page refuses a caller it cannot see as Sami before it reads anything they sent."""
    from test_live import Client
    path, _ = served
    r = Client(path, "POST /standup HTTP/1.1\r\nHost: page\r\nContent-Type: application/x-www-form-urlencoded\r\n"
                     "Content-Length: 0\r\nAccept: application/json\r\n\r\n")
    assert r.status.split()[1] == "403" and "refused" in r.body()
    r.close()
