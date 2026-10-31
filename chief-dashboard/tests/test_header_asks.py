"""Decisions waiting on Sami live in Dispatch: the page counts them and links his Dispatch Inbox, and lists none of
them (Sami, 2026-10-26: "do not rebuild parts of Dispatch in this dashboard")."""

from __future__ import annotations

import re

from test_dashboard import NOW, ask, snapshot

from chief_dashboard import v2_model, v2_render


def test_header_counts_sami_s_decisions_and_links_the_inbox_without_listing_any():
    snap = snapshot(asks=[
        ask("a1", "BET-5", "BET", 3, "Which session cache should the grader use?"),  # in task making's scope
        ask("a2", "OSSV-1", "OSSV", 5, "May the OSS lane spend $200 on reviews?"),  # in no stream's scope
        ask("a3", "BET-61", "BET", 2, "Jason's own question?"),  # assigned to Jason: not Sami's
    ])
    page = v2_model.build(snap, NOW)
    head = v2_render.top_line(page)
    assert page.asks == 2 and re.search(r'class="n">2</span> wait on you', head)
    assert f'href="{page.inbox}"' in head
    body = v2_render.body(page, {})
    assert "session cache" not in body and "May the OSS lane spend" not in body
