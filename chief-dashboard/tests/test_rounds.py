"""The learning loop section: the newest round's FINAL changes with their recipients' answers, the leads that missed it,
links to its files, and a parse that tolerates missing parts."""

from __future__ import annotations

import os
import socket
import threading

from chief_dashboard import rounds, server, v2_render

ROUND = """# Learning-loop round 2026-10-26 03:30Z — DRAFT selection (before red-team)

Missed: ORCH, VERIFY.

## Selected to spread
1. **Draft item.** -> all.

---
# FINAL after red-team (03:35Z)
* A. Work that survives a restart: ORCH 4->1->0 lanes idle; after measured (ORCH 3 min). -> TASKCOACH, FRAC. (Not INFRA: already 1.3 min.)
* B. Freeze the commit: 4 changes / 5 h slip -> 1 change. n=1. -> TASKCOACH, INFRA.
* DROPPED: 2 (already in the playbook).
"""

ADOPTIONS = """# Adoption replies
* TASKCOACH 03:26Z: A ADOPT (briefs onto issues). B NOT ADOPT (no launches this week).
* ORCH 03:26Z (answered draft items from the round file): 1 ADOPT; 6 NOT ADOPT.
* FRAC 03:27Z: A ADOPT. CANDIDATE FOR NEXT ROUND: review the whole path.
"""


def write(tmp_path, name, text):
    (tmp_path / name).write_text(text)


def test_final_changes_recipients_answers_and_missed_leads(tmp_path):
    write(tmp_path, "2026-10-25T21.md", "# older round\n# FINAL\n* A. old -> X.\n")
    write(tmp_path, "2026-10-26T03.md", ROUND)
    write(tmp_path, "2026-10-26T03-adoptions.md", ADOPTIONS)
    write(tmp_path, "2026-10-26T03-inputs.md", "inputs")
    r = rounds.load(tmp_path)
    assert (r.name, r.missed, r.final) == ("2026-10-26T03.md", "ORCH, VERIFY.", True)
    assert [(c.key, c.to) for c in r.changes] == [("A", ["TASKCOACH", "FRAC"]), ("B", ["TASKCOACH", "INFRA"])]
    assert "ORCH 4->1->0" in r.changes[0].text and r.changes[0].text.endswith("(Not INFRA: already 1.3 min.)")
    assert r.changes[1].text.startswith("Freeze the commit: 4 changes / 5 h slip -> 1 change")
    assert r.notes == ["DROPPED: 2 (already in the playbook)."]
    assert r.answer("TASKCOACH", "A") == "ADOPT" and r.answer("TASKCOACH", "B") == "NOT ADOPT"
    assert r.answer("FRAC", "A") == "ADOPT" and r.silent() == ["INFRA"]

    html = v2_render.loop_section(r)
    assert "Missed the round:</b> ORCH, VERIFY." in html
    assert 'TASKCOACH <span class="bad">✗ not adopted</span>' in html and 'INFRA <span class="dim">no reply</span>' in html
    assert "ORCH: 1 <span" in html and "No reply yet: INFRA" in html
    for name in ("2026-10-26T03.md", "2026-10-26T03-adoptions.md", "2026-10-26T03-inputs.md"):
        assert f'href="/rounds/{name}"' in html


def test_missing_parts_are_tolerated(tmp_path):
    assert rounds.load(tmp_path) is None and "no round recorded yet" in v2_render.loop_section(None)
    assert rounds.load(tmp_path / "absent") is None
    write(tmp_path, "2026-10-26T06.md", "just notes, no heading, no FINAL\n")
    r = rounds.load(tmp_path)
    assert (r.final, r.missed, r.changes, r.replies, r.adoptions_name) == (False, None, [], [], None)
    assert "No FINAL section yet." in v2_render.loop_section(r)


def test_round_files_are_served_by_exact_name_only(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "STATE_DIR", tmp_path)
    monkeypatch.setattr(rounds, "ROUNDS", tmp_path)
    write(tmp_path, "2026-10-26T03.md", ROUND)
    (tmp_path / "secret.txt").write_text("no")
    sock = tmp_path / "s.sock"
    srv = server._UnixHTTPServer(str(sock), server.make_handler(server.Dashboard(), proxy_uid=os.getuid()))
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def get(path: str) -> tuple[str, str]:
        s = socket.socket(socket.AF_UNIX)
        s.connect(str(sock))
        s.sendall(f"GET {path} HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n".encode())
        data = b""
        while chunk := s.recv(65536):
            data += chunk
        s.close()
        head, _, body = data.decode().partition("\r\n\r\n")
        return head.split()[1], body

    try:
        assert get("/rounds/2026-10-26T03.md") == ("200", ROUND)
        assert get("/rounds/secret.txt")[0] == "404"
        assert get("/rounds/..%2Fsecret.txt")[0] == "404"
    finally:
        srv.shutdown()
        srv.server_close()
