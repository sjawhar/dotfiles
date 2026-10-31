"""The standup room: the meeting is a Dispatch message thread, this page shows it and lets Sami speak into it.

What can break, and is checked here: a line from each kind of speaker (a lead, the chief of staff, Sami from this
page, Sami typing in Dispatch's own web app) is shown as the right person and only the others are spoken; a reader
who opens the page late sees every line said before; Sami's line reaches a lead's session as Sami's own words and
is written to the thread as the record; a line's audio is synthesised once and read from disk after that."""

from __future__ import annotations

import json
import os
import threading
from datetime import timedelta

import pytest

from chief_dashboard import server, standup

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
    assert "##" not in got and "<b>Waiting on:</b>" in got
    assert "<code>dispatch read --message 1</code>" in got
    assert "&lt;b&gt;not markup&lt;/b&gt;" in got  # a line is text, never markup its speaker wrote


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
