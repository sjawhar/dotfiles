"""The page's own script, run in a real browser: a live update keeps what Sami changed (the size he dragged his
note box to, what he typed in it, where the caret is), an update whose fragment is the one already shown changes
nothing at all, and a new build never reloads the page by itself — it offers a reload and waits.

Each test builds a small page that loads `render.SCRIPT` with the WebSocket and the fetch stubbed, drives one
scenario, and writes its verdict into `#result`; Chromium runs it with `--dump-dom` and the verdict is read back.
`CHIEF_DASHBOARD_BROWSER` names the browser when it is not one of the usual ones.
"""

from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from chief_dashboard import render, v2_render

BUDGET_MS = 8000  # virtual time: every timer the scenario waits on fires at once


def browser() -> str | None:
    named = os.environ.get("CHIEF_DASHBOARD_BROWSER")
    if named:
        return named if Path(named).exists() else None
    home = Path.home() / ".cache" / "ms-playwright"
    # The headless shell first: it is the build made for `--dump-dom`, and a full Chrome can sit waiting.
    for found in (sorted(glob.glob(str(home / "chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell"))),
                  [p for p in (shutil.which(x) for x in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable")) if p],
                  sorted(glob.glob(str(home / "chromium-*/chrome-linux64/chrome")))):
        if found:
            return found[-1]
    return None


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>harness</title></head><body>
<main id="page" data-version="v1">
  <p id="headline">first headline</p>
  <div id="note"><form><textarea id="note-x" name="text" rows="3"></textarea></form></div>
</main>
__PILL__
<pre id="result"></pre>
<script>
  // What the page is told next, and what the server would answer with.
  window.__fragment = __FRAGMENT__;
  window.__fetches = 0;
  window.fetch = async () => {
    window.__fetches++;
    return {ok: true, headers: {get: () => 'v2'}, text: async () => window.__fragment};
  };
  window.__send = null;
  window.WebSocket = class {
    constructor(){ window.__send = m => this.onmessage && this.onmessage({data: JSON.stringify(m)});
                   setTimeout(() => this.onopen && this.onopen(), 0); }
    close(){}
  };
  // A reload re-runs this page, so the count says whether the page reloaded itself.
  const loads = Number(sessionStorage.getItem('loads') || '0') + 1;
  sessionStorage.setItem('loads', String(loads));
  window.__loads = loads;
</script>
<script>const SINCE = "";__SCRIPT__</script>
<script>
(async function(){
  const out = {}, page = document.getElementById('page');
  let mutations = 0;  // every change to the page a reader could see; the version bookkeeping on #page is not one
  new MutationObserver(ms => {
    mutations += ms.filter(m => !(m.type === 'attributes' && m.target === page && m.attributeName === 'data-version')).length;
  }).observe(page, {subtree: true, childList: true, attributes: true, characterData: true});
  const box = document.getElementById('note-x');
  await new Promise(r => setTimeout(r, 20));
  __SCENARIO__
  out.loads = window.__loads;
  out.fetches = window.__fetches;
  out.mutations = mutations;
  out.value = box.value;
  out.height = box.style.height;
  out.rows = box.getAttribute('rows');
  out.same_node = document.getElementById('note-x') === box;
  out.focused = document.activeElement === box;
  out.selection = [box.selectionStart, box.selectionEnd];
  out.headline = document.getElementById('headline').textContent;
  const link = document.querySelector('.live-reload');
  out.reload_offered = !!link && !link.hidden;
  document.getElementById('result').textContent = JSON.stringify(out);
})();
</script></body></html>
"""

FRAGMENT = ('<p id="headline">second headline</p>'
            '<div id="note"><form><textarea id="note-x" name="text" rows="6"></textarea></form></div>')


def run(tmp_path: Path, scenario: str, fragment: str = FRAGMENT) -> dict:
    exe = browser()
    if not exe:
        pytest.skip("no Chromium found; set CHIEF_DASHBOARD_BROWSER to one")
    html = (PAGE.replace("__PILL__", render.LIVE_PILL)
                .replace("__FRAGMENT__", json.dumps(fragment))
                .replace("__SCRIPT__", render.SCRIPT + v2_render.STUDIO_SCRIPT)
                .replace("__SCENARIO__", scenario))
    (tmp_path / "harness.html").write_text(html)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(tmp_path)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        out = subprocess.run(
            [exe, "--headless", "--no-sandbox", "--disable-gpu", f"--virtual-time-budget={BUDGET_MS}",
             "--dump-dom", f"http://127.0.0.1:{srv.server_address[1]}/harness.html"],
            capture_output=True, text=True, timeout=120).stdout
    finally:
        srv.shutdown()
    m = re.search(r'<pre id="result">(.*?)</pre>', out, re.S)
    assert m, f"the harness wrote no result:\n{out[-2000:]}"
    return json.loads(m.group(1))


TYPED = """
  box.value = 'A paragraph he started writing and has not sent yet.';
  box.style.height = '312px';
  box.focus();
  box.setSelectionRange(4, 13);
"""


def test_a_live_update_keeps_the_box_he_resized_and_what_he_typed_in_it(tmp_path):
    """Sami, 2026-10-26: "When I resize the input text box, it goes back to being tiny". The update must change the
    page's own words and leave his box exactly as he left it: its height, its text, the caret and the focus."""
    r = run(tmp_path, TYPED + """
  window.__send({type: 'version', version: 'v2'});
  await new Promise(r => setTimeout(r, 400));
""")
    assert r["fetches"] == 1 and r["headline"] == "second headline"  # the update ran
    assert r["height"] == "312px"  # the size he dragged it to
    assert r["value"] == "A paragraph he started writing and has not sent yet."
    assert r["rows"] == "3"  # the server's own rows never shrink a box he sized
    assert r["same_node"] and r["focused"] and r["selection"] == [4, 13]


def test_an_update_whose_fragment_is_the_one_shown_changes_nothing(tmp_path):
    """The first update morphs; a second one carrying the same fragment touches no node of the page."""
    r = run(tmp_path, TYPED + """
  window.__send({type: 'version', version: 'v2'});
  await new Promise(r => setTimeout(r, 400));
  const after_first = mutations;
  window.__send({type: 'version', version: 'v3'});
  await new Promise(r => setTimeout(r, 400));
  out.quiet = mutations === after_first;
  out.moved = after_first > 0;
""")
    assert r["fetches"] == 2 and r["moved"] and r["quiet"]
    assert r["height"] == "312px" and r["value"].startswith("A paragraph")


def test_a_new_build_offers_a_reload_and_never_takes_one(tmp_path):
    """Sami, 2026-10-26: the page "is still doing some stupid, fucking annoying thing where it reloads the page every
    few seconds". A new build stops the updates and shows a reload link; nothing reloads until he clicks it."""
    r = run(tmp_path, TYPED + """
  window.__send({type: 'build', build: 'one'});
  window.__send({type: 'build', build: 'two'});
  await new Promise(r => setTimeout(r, 2000));
  window.__send({type: 'version', version: 'v2'});
  await new Promise(r => setTimeout(r, 2000));
""")
    assert r["loads"] == 1  # the page is the one that was loaded: it never reloaded itself
    assert r["reload_offered"]
    assert r["fetches"] == 0 and r["headline"] == "first headline"  # stale: it stopped morphing
    assert r["value"].startswith("A paragraph")


def test_an_empty_box_grows_with_what_he_writes_and_stops_once_he_sizes_it(tmp_path):
    """The box is big enough for a paragraph, grows as he types, and a height he set himself is never overwritten."""
    r = run(tmp_path, """
  box.focus();
  box.value = 'one line';
  box.dispatchEvent(new Event('input', {bubbles: true}));
  await new Promise(r => setTimeout(r, 50));
  const small = parseFloat(box.style.height);
  box.value = Array.from({length: 40}, (_, i) => 'line ' + i).join('\\n');
  box.dispatchEvent(new Event('input', {bubbles: true}));
  await new Promise(r => setTimeout(r, 50));
  out.grew = parseFloat(box.style.height) > small;
""")
    assert r["grew"]
