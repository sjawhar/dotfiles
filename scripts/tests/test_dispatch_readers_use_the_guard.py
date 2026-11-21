#!/usr/bin/env python3
"""Every file here that sends a bearer to Dispatch outside its client runs scripts/agent-private-guard
before each request (scripts/agent-private-check's header says why).

A reader is a file of the checkout (`git ls-files --cached --others --exclude-standard`: tracked, and
new files not yet added) that is neither a test (a tests, __tests__ or test path segment, a test_ or
.test. name) nor documentation (.md) nor a link, and that holds a Dispatch API path, `/api/v1`,
`/ws/live` or `/ws/doc/`, outside a comment. The rule a reader must pass:
  - Python: in every function that sends a request, each send (a call of urlopen,
    socket.create_connection or http.client's connections) and each urllib.request.Request built
    before it come after a call of `private_guard(...)` made since the previous send, in evaluation
    order, unless OTHER_SERVICES names the function with the service it talks to instead. (The
    order is the source's: a guard call inside a branch that does not run still counts.) That
    `private_guard` is defined in the reader, or imported from a module beside it (a relative
    import), as a function that runs `agent-private-guard` (a module-level constant names it and the function
    calls subprocess.run). A module whose Dispatch requests all go through another module's client
    (chief-dashboard's dispatch_check.py, videos.py) sends none of its own, so it passes as it is.
  - Any other file: a line that is not a comment names `agent-private-guard`.
GUARD_ITSELF, scripts/agent-private-check and agent-private-guard, is the rule, not a reader of it.
A file that only runs the `dispatch` command holds no Dispatch path, and the Dispatch client holds
the rule itself. scripts/pod-memory-peak names the Kubernetes API's /api/v1 in a comment, so it is no reader.
"""

from __future__ import annotations

import ast
import io
import re
import shutil
import subprocess
import tempfile
import tokenize
import unittest
from pathlib import Path

DOTFILES = Path(__file__).resolve().parents[2]
DISPATCH_PATH = re.compile(r"/api/v1(?![\w.-])|/ws/live|/ws/doc/")
GUARD = "agent-private-guard"
TEST_SEGMENTS = {"tests", "__tests__", "test"}
SEND_CALLS = {"urlopen", "create_connection", "HTTPConnection", "HTTPSConnection"}
REQUEST_CALLS = SEND_CALLS | {"Request"}
GUARD_ITSELF = {"scripts/agent-private-check", "scripts/agent-private-guard"}
# Request functions of a reader that talk to another service, never to Dispatch, with that service.
OTHER_SERVICES = {
    ("chief-dashboard/chief_dashboard/sources.py", "_open"): "sends a request its caller built and guarded",
    ("chief-dashboard/chief_dashboard/sources.py", "envoy_send"): "the Envoy hub",
    ("chief-dashboard/chief_dashboard/sources.py", "envoy_sessions"): "the Envoy hub",
    ("chief-dashboard/chief_dashboard/sources.py", "envoy_role"): "the Envoy hub",
    ("chief-dashboard/chief_dashboard/standup.py", "ElevenLabs.mp3"): "the ElevenLabs voice service",
    ("chief-dashboard/chief_dashboard/dispatch_check.py", "GitHub.gql"): "GitHub's GraphQL API",
}
KNOWN_READERS = {
    "scripts/sami-said",
    "chief-dashboard/chief_dashboard/sources.py",
    "chief-dashboard/chief_dashboard/standup.py",
    "chief-dashboard/chief_dashboard/dispatch_check.py",
    "chief-dashboard/chief_dashboard/videos.py",
    "plugins/sjawhar/skills/reflect/scripts/extract-dispatch-human.py",
}


def checkout_files(root: Path) -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=root,
                         capture_output=True, check=True).stdout
    return sorted({p for p in out.decode().split("\0") if p})


def in_scope(rel: str) -> bool:
    parts = rel.split("/")
    name = parts[-1]
    return (not rel.endswith(".md") and not TEST_SEGMENTS & set(parts[:-1])
            and not name.startswith("test_") and ".test." not in name)


def is_python(rel: str, text: str) -> bool:
    return rel.endswith(".py") or (text.startswith("#!") and "python" in text.split("\n", 1)[0])


def without_comments(rel: str, text: str) -> str:
    """TEXT with its comments blanked: Python's by its tokenizer, any other file's `#` and `//` lines."""
    if is_python(rel, text):
        lines = text.splitlines(keepends=True)
        try:
            for tok in tokenize.generate_tokens(io.StringIO(text).readline):
                if tok.type == tokenize.COMMENT:
                    row, col = tok.start
                    lines[row - 1] = lines[row - 1][:col] + "\n"
        except (tokenize.TokenError, SyntaxError):
            pass
        return "".join(lines)
    return "".join(line for line in text.splitlines(keepends=True) if not line.lstrip().startswith(("#", "//")))


def readers(root: Path, files: list[str]) -> list[str]:
    found = []
    for rel in files:
        path = root / rel
        if not in_scope(rel) or path.is_symlink() or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if DISPATCH_PATH.search(without_comments(rel, text)):
            found.append(rel)
    return found


def called(node: ast.AST) -> str | None:
    """The name a call calls: `f` of f(…), `g` of x.y.g(…)."""
    func = node.func if isinstance(node, ast.Call) else None
    return func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None


def functions(tree: ast.Module) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    """Every function by its qualified name (Class.method, outer.inner)."""
    found: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found[prefix + child.name] = child
                walk(child, f"{prefix}{child.name}.")
            elif isinstance(child, ast.ClassDef):
                walk(child, f"{prefix}{child.name}.")
            else:
                walk(child, prefix)

    walk(tree, "")
    return found


def own_calls(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.Call]:
    """The calls in FN's own body, not in a function or class defined inside it, in source order."""
    calls: list[ast.Call] = []

    def walk(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                continue
            if isinstance(child, ast.Call):
                calls.append(child)
            walk(child)

    walk(fn)
    return sorted(calls, key=lambda c: (c.lineno, c.col_offset))


def runs_the_guard(tree: ast.Module) -> bool:
    """Whether TREE defines a `private_guard` that runs agent-private-guard: a module-level constant names
    it, and the function calls subprocess.run."""
    names_it = any(isinstance(node, (ast.Assign, ast.AnnAssign)) and any(
        isinstance(n, ast.Constant) and isinstance(n.value, str) and GUARD in n.value for n in ast.walk(node))
        for node in tree.body)
    fn = functions(tree).get("private_guard")
    return names_it and fn is not None and any(called(c) == "run" for c in own_calls(fn))


def module_file(rel: str, node: ast.ImportFrom) -> str:
    """The file a relative import in REL names: `from .m import …` beside it, `from ..m import …` one up."""
    return str(Path(rel).parents[node.level - 1] / ((node.module or "").replace(".", "/") + ".py"))


def guard_source(root: Path, rel: str, tree: ast.Module) -> bool:
    """Whether the `private_guard` REL calls runs agent-private-guard: its own, or one it imports."""
    if "private_guard" in functions(tree):
        return runs_the_guard(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level and any(a.name == "private_guard" for a in node.names):
            path = root / module_file(rel, node)
            return path.is_file() and runs_the_guard(ast.parse(path.read_text(encoding="utf-8")))
    return False


def unguarded_requests(root: Path, rel: str) -> list[str]:
    """REL's request functions that do not run the guard first, as `REL: qualname`."""
    text = (root / rel).read_text(encoding="utf-8")
    if not is_python(rel, text):
        return [] if GUARD in without_comments(rel, text) else [f"{rel}: names no {GUARD}"]
    tree = ast.parse(text)
    guarded_by = guard_source(root, rel, tree)
    missing = []
    for name, fn in functions(tree).items():
        if (rel, name) in OTHER_SERVICES:
            continue
        # Evaluation order: a call's arguments (urlopen(Request(...))) finish before the call.
        calls = sorted(own_calls(fn), key=lambda c: (c.end_lineno or 0, c.end_col_offset or 0))
        armed, bare = False, False
        for call in calls:
            kind = called(call)
            if kind == "private_guard":
                armed = guarded_by
            elif kind in REQUEST_CALLS:
                bare = bare or not armed
                if kind in SEND_CALLS:
                    armed = False  # one guard answers for one request
        if bare:
            missing.append(f"{rel}: {name}")
    return missing


def unguarded(root: Path, files: list[str]) -> list[str]:
    """Every request function of a reader among FILES that does not run the guard first."""
    return sorted(m for rel in readers(root, files) if rel not in GUARD_ITSELF for m in unguarded_requests(root, rel))


class DispatchReadersUseTheGuard(unittest.TestCase):
    def test_every_dispatch_request_runs_the_guard_first(self) -> None:
        self.assertEqual(unguarded(DOTFILES, checkout_files(DOTFILES)), [])

    def test_the_scan_reads_the_checkout_and_finds_the_known_readers(self) -> None:
        files = checkout_files(DOTFILES)
        self.assertGreater(len(files), 1000, "git ls-files listed almost nothing: the scan would be blind")
        found = set(readers(DOTFILES, files)) - GUARD_ITSELF
        self.assertEqual(found, KNOWN_READERS)
        self.assertNotIn("scripts/pod-memory-peak", found)  # the Kubernetes API, in a comment
        # Every function OTHER_SERVICES names still sends a request, so the list cannot hide a new one.
        for rel, name in OTHER_SERVICES:
            fn = functions(ast.parse((DOTFILES / rel).read_text(encoding="utf-8"))).get(name)
            self.assertIsNotNone(fn, f"{rel} has no {name}")
            self.assertTrue(fn and any(called(c) in REQUEST_CALLS for c in own_calls(fn)), f"{rel}: {name} sends nothing")

    def control(self, rel: str, change: tuple[str, str] | None = None, append: str = "") -> list[str]:
        """unguarded() over the known readers copied to a scratch tree, with one text replaced in REL, or
        APPEND added to its end."""
        root = Path(tempfile.mkdtemp(prefix="guard-control-"))
        self.addCleanup(shutil.rmtree, root, True)
        for reader in KNOWN_READERS:
            (root / reader).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(DOTFILES / reader, root / reader)
        self.assertEqual(unguarded(root, sorted(KNOWN_READERS)), [], "the scratch tree fails before the control")
        text = (root / rel).read_text(encoding="utf-8")
        if change is not None:
            old, new = change
            self.assertEqual(text.count(old), 1, f"the control's text is not in {rel} exactly once: {old!r}")
            text = text.replace(old, new)
        (root / rel).write_text(text + append, encoding="utf-8")
        return unguarded(root, sorted(KNOWN_READERS))

    def test_control_sami_said_without_the_guard_call_is_named(self) -> None:
        # The call alone goes; the guard's constant and its helper stay.
        found = self.control("scripts/sami-said", ("    private_guard(cfg)\n    url = cfg.url", "    url = cfg.url"))
        self.assertEqual(found, ["scripts/sami-said: api_get"])

    def test_control_the_live_socket_without_the_guard_call_is_named(self) -> None:
        found = self.control("scripts/sami-said", ("        private_guard(cfg)\n        self.timeout", "        self.timeout"))
        self.assertEqual(found, ["scripts/sami-said: LiveSocket.__init__"])

    def test_control_standup_without_its_guard_call_is_named(self) -> None:
        # standup.py takes Dispatch from sources.py but posts its own message; its import of
        # private_guard stays, only the call goes.
        rel = "chief-dashboard/chief_dashboard/standup.py"
        found = self.control(rel, ('        private_guard(server, cfg["token"])\n', ""))
        self.assertEqual(found, [f"{rel}: Room._post_message"])

    def test_control_a_guard_helper_that_does_not_run_the_guard_is_named(self) -> None:
        rel = "chief-dashboard/chief_dashboard/sources.py"
        found = self.control(rel, ('"agent-private-guard"', '"some-other-script"'))
        self.assertEqual(found, [f"{rel}: Dispatch.get", f"{rel}: Dispatch.open",
                                 "chief-dashboard/chief_dashboard/standup.py: Room._post_message"])

    def test_control_a_new_request_function_is_named(self) -> None:
        rel = "chief-dashboard/chief_dashboard/videos.py"
        stray = ("\n\ndef stray(server, token):\n    return urllib.request.urlopen(urllib.request.Request(\n"
                 "        f'{server}/api/v1/issues', headers={'Authorization': f'Bearer {token}'}))\n")
        self.assertEqual(self.control(rel, append=stray), [f"{rel}: stray"])

    def test_control_a_second_request_after_one_guard_call_is_named(self) -> None:
        rel = "scripts/sami-said"
        twice = ("    with urllib.request.urlopen(req, timeout=30) as resp:\n",
                 "    urllib.request.urlopen(req, timeout=30).close()\n    with urllib.request.urlopen(req, timeout=30) as resp:\n")
        self.assertEqual(self.control(rel, twice), [f"{rel}: api_get"])


if __name__ == "__main__":
    unittest.main()
