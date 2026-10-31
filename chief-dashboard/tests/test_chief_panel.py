"""The chief-of-staff panel: a note box that sends over Envoy through /note and a link into its live conversation,
and nothing from Dispatch. Each stream keeps its note box beside its video, with its lead's live conversation link
under it."""

from __future__ import annotations

import re
import urllib.parse

from test_dashboard import NOW, snapshot

from chief_dashboard import server, v2_model, v2_render

WEB = "https://dispatch.example"



def test_panel_links_the_chief_live_conversation_and_posts_a_note_to_it():
    page = v2_model.build(snapshot(), NOW)
    html = v2_render.chief_panel(page, [], True, True)
    assert f'href="{WEB}/agents/{v2_render.CHIEF_SESSION}/live"' in html and "running now" in html
    assert re.search(r'<form class="note-form" method="post" action="/note" data-async="1"><input type="hidden" '
                     r'name="stream" value="chief">', html)
    assert "/asks" not in html and "Ready for you" not in html


def test_panel_lists_what_sami_asked_with_its_state_status_and_result_link():
    page = v2_model.build(snapshot(), NOW)
    at = (NOW - v2_render.timedelta(hours=1)).isoformat()
    old = (NOW - v2_render.timedelta(days=5)).isoformat()
    ledger = [
        {"id": "rocq-speed", "at": at, "state": "needs-you", "title": "Which Rocq speed lever next",
         "said": "SAID-TEXT", "status": "Two levers measured; <yours> to pick",
         "answer_url": "https://dispatch.example/issues/INFRA-323/documents/rocq-speed-md"},
        {"id": "unlinked", "at": at, "state": "not-done", "title": "Key back", "status": "Nobody on it"},
        {"id": "old-done", "at": old, "state": "done", "title": "Old", "answer_url": "https://d/x"},
        {"id": "old-open", "at": old, "state": "running", "title": "Still open"},
    ]
    html = v2_render.chief_panel(page, [], True, True, ledger=ledger)
    assert "What you asked me (3)" in html
    assert ('Which Rocq speed lever next <span class="lbl bad">needs you</span>' in html
            and '<a href="https://dispatch.example/issues/INFRA-323/documents/rocq-speed-md">result</a>' in html
            and "Two levers measured; &lt;yours&gt; to pick" in html)
    assert 'Key back <span class="lbl bad">not done</span>' in html and "Nobody on it" in html
    assert "Still open" in html and "SAID-TEXT" not in html and "Old" not in html


def test_a_chief_note_goes_to_the_chief_session_over_envoy(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "STATE_DIR", tmp_path)
    sent = []
    monkeypatch.setattr(server, "envoy_send", lambda sid, msg, idempotency_key: sent.append((sid, msg)) or {"recipient": "x"})
    d = server.Dashboard()
    r = d.send_note(server.CHIEF, "Where are last night's results?", "sami@theorem.dev")
    assert r["delivery"] == "delivered" and sent[0][0] == server.CHIEF_SESSION
    assert "chief of staff" in sent[0][1] and "Where are last night's results?" in sent[0][1]
    assert [n["text"] for n in d.notes()[server.CHIEF]] == ["Where are last night's results?"]


def test_a_fifteen_thousand_character_note_is_sent_whole_and_the_boxes_admit_it(tmp_path, monkeypatch):
    """Sami, 2026-10-26: the box "has a limit that I find too low. It has cut me off twice tonight." A note of
    15,000 characters reaches the lead's session whole, is recorded whole, and every note box on the page says it
    will take it."""
    monkeypatch.setattr(server, "STATE_DIR", tmp_path)
    sent = []
    monkeypatch.setattr(server, "envoy_send",
                        lambda sid, msg, idempotency_key: sent.append((sid, msg)) or {"recipient": "x"})
    long_note = "Here is the whole argument. " * 536  # 15,008 characters
    assert 15_000 <= len(long_note) <= server.MAX_NOTE
    d = server.Dashboard()
    r = d.send_note(server.CHIEF, long_note, "sami@theorem.dev")
    assert r["delivery"] == "delivered"
    assert long_note.strip() in sent[0][1] and len(sent[0][1]) > 15_000  # the whole note, not a cut of it
    assert d.notes()[server.CHIEF][-1]["text"] == long_note.strip()

    # The form it is typed into accepts it, and so does the POST the page makes of it.
    page = v2_model.build(snapshot(), NOW)
    boxes = re.findall(r'<textarea[^>]*maxlength="(\d+)"', v2_render.chief_panel(page, [], True, True)
                       + v2_render.body(page, {}, notes={}, can_send=True))
    assert boxes and {int(x) for x in boxes} == {server.MAX_NOTE} and server.MAX_NOTE >= 20_000
    encoded = len(urllib.parse.urlencode({"stream": "chief", "back": "/", "text": long_note}).encode())
    assert encoded <= server.MAX_NOTE * 12 + 4096  # the body cap the POST route applies


def test_each_stream_keeps_its_note_box_beside_the_player_and_links_its_lead_live_conversation():
    snap = snapshot()
    snap["explainers"] = {"BET-83": [{"stem": "explainer", "name": "explainer.mp4", "sha": "a" * 64, "seconds": 9.0,
                                      "title": "Standup", "uploaded": "2026-10-24T10:00:00Z"}]}
    page = v2_model.build(snap, NOW)
    v = next(x for x in page.streams if x.st.issue == "BET-83")
    html = v2_render.stream_section(v, page, snap["explainers"]["BET-83"], [], True)
    studio = html[html.index('<div class="studio">'):html.index('<details class="srest"')]
    assert '<video id="v-task-making" class="vplay"' in studio
    assert 'action="/note"' in studio and 'name="stream" value="task-making"' in studio and "<textarea" in studio
    assert f'href="{WEB}/agents/s-1/live"' in studio
    # The plan and the rest start folded.
    assert '<details class="srest" id="rest-task-making">' in html


def test_scoreboard_links_show_in_the_stream_header_and_unsafe_ones_are_dropped():
    snap = snapshot()
    data = snap["scoreboards"]["BET-83"]["versions"][-1]["data"]
    data["links"] = [{"label": "Eval results (LINUX-29)", "url": "dispatch://LINUX-29/artifact/eval-results-md"},
                     {"label": "Report", "url": "https://example.com/r"},
                     {"label": "Bad", "url": "javascript:alert(1)"}, {"url": "https://example.com/nolabel"}]
    page = v2_model.build(snap, NOW)
    v = next(x for x in page.streams if x.st.issue == "BET-83")
    head = v2_render.stream_section(v, page, [])
    head = head[head.index('<div class="sh-right">'):head.index("</div></div>")]
    assert ">Eval results (LINUX-29) ›</a>" in head and f"{WEB}/issues/LINUX-29/" in head
    assert 'href="https://example.com/r"' in head
    assert "javascript:" not in head and "nolabel" not in head
