#!/usr/bin/env python3
"""scripts/pr-gate run|merge against fake gh and omp binaries."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

GATE = Path(__file__).parents[1] / "pr-gate"
REPO = "sjawhar/legion-smoke"
A = "a" * 40
B = "b" * 40
BET = "theorem-labs/binary-equivalence-taiga"

# State lives in $STUB_STATE (JSON): heads (consumed one per head read; the last one sticks),
# rollup, comments, merged. Every call is logged to $STUB_LOG.
GH_STUB = r'''#!/usr/bin/env python3
import json, os, sys
st_path = os.environ["STUB_STATE"]
st = json.load(open(st_path))
args = sys.argv[1:]
open(os.environ["STUB_LOG"], "a").write(json.dumps(args) + "\n")
def save(): json.dump(st, open(st_path, "w"))
def head():
    h = st["heads"][0]
    if len(st["heads"]) > 1: st["heads"].pop(0)
    save(); return h
if args[0] == "api" and args[-1].endswith("/files?per_page=100"):
    files = st.get("pr_files", [])
    print(json.dumps([[{"filename": f} for f in files[i:i + 100]] for i in range(0, len(files), 100)] or [[]]))
elif args[0] == "api" and "/pulls/" in args[1] and ".changed_files" in args:
    print(st.get("changed_files", len(st.get("pr_files", []))))
elif args[0] == "api" and "/pulls/" in args[1]:
    print(head())
elif args[0] == "api" and "/compare/" in args[1]:
    print(json.dumps({"status": st.get("compare_status", "ahead"), "files": st.get("compare_files", [])}))
elif args[0] == "api" and "/issues/comments/" in args[1]:
    cid = int(args[1].rsplit("/", 1)[1])
    print(next(c["body"] for c in st["comments"] if c["id"] == cid))
elif args[0] == "api" and "--paginate" in args:
    print(json.dumps([st["comments"]]))
elif args[:2] == ["pr", "comment"]:
    body = open(args[args.index("--body-file") + 1]).read()
    cid = 1000 + len(st["comments"])
    url = f"https://github.com/x/y/pull/1#issuecomment-{cid}"
    st["comments"].append({"id": cid, "body": body, "created_at": f"2026-10-17T12:{cid % 60:02d}:00Z", "html_url": url})
    save(); print(url)
elif args[:2] == ["pr", "view"]:
    fields = args[args.index("--json") + 1]
    if "--jq" in args and fields == "headRefOid":  # review-app-approve.sh's own reads
        print(st["heads"][0])
    elif "--jq" in args and fields == "statusCheckRollup":
        print("")
    elif "statusCheckRollup" in fields:
        print(json.dumps({"statusCheckRollup": st["rollup"], "headRefOid": st["heads"][0], "state": st.get("pr_state", "OPEN")}))
    else:
        print(json.dumps({"state": "MERGED" if st.get("merged") else "OPEN", "mergeCommit": {"oid": "c" * 40} if st.get("merged") else None}))
elif args[:2] == ["pr", "diff"]:
    print("diff --git a/tasks/a.py b/tasks/a.py")
elif args[:2] == ["pr", "merge"]:
    st["merged"] = True; st["merge_env"] = os.environ.get("PR_GATE_MERGE"); save()
else:
    sys.exit(f"unexpected gh {args}")
'''

# Emits $STUB_EVENTS (a JSON list of events) as JSONL; with $STUB_MOVE_HEAD set, the head moves.
OMP_STUB = r'''#!/usr/bin/env python3
import json, os, sys
open(os.environ["STUB_LOG"], "a").write(json.dumps(["omp", *sys.argv[1:]]) + "\n")
# Like this omp build, which disables find and ast_grep: an unavailable --tools name fails at start.
if "--tools" in sys.argv:
    off = set(os.environ.get("STUB_UNAVAILABLE_TOOLS", "find,ast_grep").split(","))
    bad = [t for t in sys.argv[sys.argv.index("--tools") + 1].split(",") if t in off]
    if bad:
        sys.exit(f"Error: Built-in tools unavailable in this session: {', '.join(bad)}.\nRun `omp --help` for available flags.")
if os.environ.get("STUB_CWD_LOG"):
    files = sorted(os.path.relpath(os.path.join(d, f)) for d, _, fs in os.walk(".") for f in fs)
    json.dump({"cwd": os.getcwd(), "files": files}, open(os.environ["STUB_CWD_LOG"], "w"))
if os.environ.get("STUB_MOVE_HEAD"):
    p = os.environ["STUB_STATE"]; st = json.load(open(p)); st["heads"] = [os.environ["STUB_MOVE_HEAD"]]
    json.dump(st, open(p, "w"))
for e in json.loads(os.environ["STUB_EVENTS"]):
    print(json.dumps(e))
'''


def end(text, provider="anthropic", model="claude-opus-5-5", cost=0.5, stop="stop", error=None):
    m = {"role": "assistant", "provider": provider, "model": model, "stopReason": stop,
         "usage": {"cost": {"total": cost}}, "content": [{"type": "text", "text": text}]}
    if error:
        m["errorMessage"] = error
    return {"type": "message_end", "message": m}


def check(name, conclusion="SUCCESS", started="2026-10-17T10:00:00Z", workflow="CI", status="COMPLETED"):
    return {"__typename": "CheckRun", "name": name, "workflowName": workflow, "status": status,
            "conclusion": conclusion, "startedAt": started}


def gate_comment(gate, sha, verdict, at, cid):
    return {"id": cid, "created_at": at, "html_url": f"https://x/{cid}",
            "body": f"**Gate: {gate} at {sha}** — {verdict}\n\n<!-- pr-gate gate={gate} head={sha} verdict={verdict} model=m -->"}


class PrGateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        for name, body in (("gh", GH_STUB), ("omp", OMP_STUB)):
            (d / name).write_text(body)
            (d / name).chmod(0o755)
        self.state = d / "state.json"
        self.log = d / "calls.log"
        self.brief = d / "brief.md"
        self.brief.write_text("Review the docs change.")
        self.out = d / "out"
        self.env = {**os.environ, "PATH": f"{d}:{os.environ['PATH']}", "STUB_STATE": str(self.state),
                    "STUB_LOG": str(self.log)}
        self.set_state(heads=[A], rollup=[check("gate")], comments=[])

    def tearDown(self):
        self.tmp.cleanup()

    def set_state(self, **kw):
        self.state.write_text(json.dumps(kw))

    def st(self):
        return json.loads(self.state.read_text())

    def calls(self):
        return [json.loads(l) for l in self.log.read_text().splitlines()] if self.log.exists() else []

    def pr_gate(self, *args, **env):
        return subprocess.run([str(GATE), *args], env={**self.env, **env}, capture_output=True, text=True)

    def run_gate(self, events, *extra, **env):
        return self.pr_gate("run", "--repo", REPO, "--pr", "1", "--gate", "review", "--brief", str(self.brief),
                            "--out", str(self.out), *extra, STUB_EVENTS=json.dumps(events), **env)

    def merge(self, *extra):
        return self.pr_gate("merge", "--repo", REPO, "--pr", "1", "--require", "review", *extra)

    # --- run ---

    def test_approve_posts_comment_naming_head_with_summed_cost_and_marker(self):
        r = self.run_gate([end("thinking", cost=0.25), end("VERDICT: APPROVE\nLooks right.", cost=0.5)])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("timeout: 0", r.stdout.splitlines()[0])
        [c] = self.st()["comments"]
        self.assertEqual(c["body"].splitlines()[0], f"**Gate: review at {A}** — APPROVE")
        self.assertNotRegex(c["body"], r"\$\s*\d")
        self.assertIn("$0.75", r.stdout)
        self.assertEqual(json.loads((self.out / "summary.json").read_text())["cost_usd"], 0.75)
        self.assertIn("Looks right.", c["body"])
        self.assertIn(f"<!-- pr-gate gate=review head={A} verdict=APPROVE", c["body"])
        omp = next(c for c in self.calls() if c[0] == "omp")
        self.assertIn(A, omp[-1].splitlines()[0])
        self.assertEqual(omp[omp.index("--model") + 1], "anthropic/claude-opus-5-5:xhigh")

    def test_head_moved_mid_run_posts_verdict_marked_not_valid_and_exits_5(self):
        r = self.run_gate([end("VERDICT: APPROVE\nok")], STUB_MOVE_HEAD=B)
        self.assertEqual(r.returncode, 5)
        self.assertIn(A, r.stderr)
        self.assertIn(B, r.stderr)
        [c] = self.st()["comments"]
        self.assertEqual(c["body"].splitlines()[0],
                         f"**Gate: review at {A}** — APPROVE (head has since moved to {B}; not valid for merge)")
        self.assertIn(f"<!-- pr-gate gate=review head={A} verdict=APPROVE", c["body"])
        # merge at the moved head must refuse the comment the run just posted
        st = self.st()
        st["rollup"] = [check("gate")]
        self.set_state(**st)
        self.assert_refused(self.merge(), f"names {A}, not head {B}")

    def test_since_puts_delta_range_and_files_before_the_brief(self):
        st = self.st()
        st["compare_files"] = ["docs/a.md", "src/b.py"]
        self.set_state(**st)
        r = self.run_gate([end("VERDICT: APPROVE\nok")], "--since", B)
        self.assertEqual(r.returncode, 0, r.stderr)
        prompt = next(c for c in self.calls() if c[0] == "omp")[-1]
        self.assertIn(f"Review only the delta {B}..{A}, which follows a reviewed head", prompt)
        self.assertIn("- docs/a.md\n- src/b.py\n", prompt)
        self.assertLess(prompt.index("src/b.py"), prompt.index("Review the docs change."))
        self.assertTrue(any(c[0] == "api" and c[1].endswith(f"/compare/{B}...{A}") for c in self.calls()))

    def test_hdo_repo_refuses_non_anthropic_model_before_any_run(self):
        for repo, model in (("theorem-labs/foo-hdo", "openai/gpt-6-astra:xhigh"),
                            ("Theorem-Labs/bar-HDO", "google/gemini-3-pro")):
            r = self.pr_gate("run", "--repo", repo, "--pr", "1", "--gate", "review", "--brief", str(self.brief),
                             "--model", model, STUB_EVENTS="[]")
            self.assertEqual(r.returncode, 1)
            self.assertEqual(len(r.stderr.strip().splitlines()), 1)
            self.assertIn("HDO material never reaches a non-Claude agent", r.stderr)
        self.assertFalse(any(c[0] == "omp" for c in self.calls()))
        self.assertEqual(self.st()["comments"], [])

    def test_hdo_repo_runs_anthropic_model(self):
        r = self.pr_gate("run", "--repo", "theorem-labs/foo-hdo", "--pr", "1", "--gate", "review",
                         "--brief", str(self.brief), "--out", str(self.out),
                         STUB_EVENTS=json.dumps([end("VERDICT: APPROVE\nok")]))
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_provider_fallback_posts_nothing(self):
        r = self.run_gate([end("x"), end("VERDICT: APPROVE\nok", provider="openai", model="gpt-6-astra")])
        self.assertEqual(r.returncode, 3)
        self.assertEqual(self.st()["comments"], [])

    def test_model_fallback_same_provider_posts_nothing(self):
        r = self.run_gate([end("VERDICT: APPROVE\nok", model="claude-sonnet-5")])
        self.assertEqual(r.returncode, 3)
        self.assertEqual(self.st()["comments"], [])

    def test_provider_refusal_posts_nothing(self):
        r = self.run_gate([end("VERDICT: APPROVE\nok"), end("", stop="error", error="policy refusal")])
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.st()["comments"], [])

    def test_missing_verdict_line_posts_nothing(self):
        r = self.run_gate([end("I think it is fine.\nVERDICT: APPROVE")])
        self.assertEqual(r.returncode, 4)
        self.assertEqual(self.st()["comments"], [])

    def test_openai_model_runs_and_posts_without_a_dollar_amount(self):
        r = self.run_gate([end("VERDICT: REQUEST_CHANGES\nFix it.", provider="openai", model="gpt-6-astra", cost=1.25)],
                          "--model", "openai/gpt-6-astra:xhigh")
        self.assertEqual(r.returncode, 0, r.stderr)
        omp = next(c for c in self.calls() if c[0] == "omp")
        self.assertEqual(omp[omp.index("--model") + 1], "openai/gpt-6-astra:xhigh")
        [c] = self.st()["comments"]
        self.assertEqual(c["body"].splitlines()[0], f"**Gate: review at {A}** — REQUEST_CHANGES")
        self.assertIn("`openai`", c["body"])
        self.assertNotRegex(c["body"], r"\$\s*\d")
        self.assertEqual(json.loads((self.out / "summary.json").read_text())["cost_usd"], 1.25)

    def test_legion_without_checkout_refused_before_any_run(self):
        r = self.pr_gate("run", "--repo", "sjawhar/legion", "--pr", "1", "--gate", "review", "--brief",
                         str(self.brief), STUB_EVENTS="[]", PR_GATE_LEGION_CHECKOUT="")
        self.assertEqual(r.returncode, 1)
        self.assertIn("--legion-checkout", r.stderr)
        self.assertFalse(any(c[0] == "omp" for c in self.calls()))

    # --- HDO-derived paths ---

    def bet_setup(self, files):
        """A local BET stand-in whose PR head holds PEE paths, served as PR_GATE_GIT_URL."""
        d = Path(self.tmp.name)
        src = d / "bet-src"
        for p in ["README.md", "tasks/a.py", "experiments/2026-09-26--sami--pee-screen/items.json",
                  "experiments/2026-09-26--sami--proof-tree-screen/tasks.json",
                  "tasks/proof-tasks/sources/pee/pee_launch.py", "tasks/tests/replay/r1.json"]:
            (src / p).parent.mkdir(parents=True, exist_ok=True)
            (src / p).write_text(p)
        g = lambda *a: subprocess.run(["git", "-C", str(src), *a], check=True, capture_output=True, text=True).stdout
        g("init", "-q")
        g("add", ".")
        g("-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "commit", "-qm", "i")
        sha = g("rev-parse", "HEAD").strip()
        g("update-ref", "refs/pull/1/head", sha)
        g("config", "uploadpack.allowFilter", "true")
        self.local = d / "hdo-paths.local"
        self.local.write_text(f"{BET}:{d}/pee-outputs/**\n")
        self.set_state(heads=[sha], rollup=[check("gate")], comments=[], pr_files=files)
        self.bet_env = {"PR_GATE_GIT_URL": f"file://{src}", "PR_GATE_HDO_LOCAL": str(self.local)}
        return sha

    def run_bet(self, events, model, *extra, **env):
        return self.pr_gate("run", "--repo", BET, "--pr", "1", "--gate", "review", "--brief", str(self.brief),
                            "--out", str(self.out), "--model", model, *extra,
                            STUB_EVENTS=json.dumps(events), **{**self.bet_env, **env})

    def test_pr_touching_pee_path_refuses_openai_and_allows_anthropic(self):
        files = [f"src/f{i}.py" for i in range(150)] + ["experiments/2026-09-26--sami--pee-screen/local/x/control-key.json"]
        self.bet_setup(files)
        r = self.run_bet([end("VERDICT: APPROVE\nok", provider="openai", model="gpt-6-astra")], "openai/gpt-6-astra:xhigh")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertEqual(len(r.stderr.strip().splitlines()), 1, r.stderr)
        self.assertIn("experiments/2026-09-26--sami--pee-screen/local/x/control-key.json", r.stderr)
        self.assertIn("pr-gate-hdo-paths", r.stderr)
        self.assertFalse(any(c[0] == "omp" for c in self.calls()))
        self.assertEqual(self.st()["comments"], [])
        r = self.run_bet([end("VERDICT: APPROVE\nok")], "anthropic/claude-opus-5-5:xhigh")
        self.assertEqual(r.returncode, 0, r.stderr)
        omp = next(c for c in self.calls() if c[0] == "omp")
        self.assertNotIn("--tools", omp)

    def test_truncated_file_list_refuses_openai(self):
        self.bet_setup(["src/a.py"])
        st = self.st()
        st["changed_files"] = 3001
        self.set_state(**st)
        r = self.run_bet([], "openai/gpt-6-astra:xhigh")
        self.assertEqual(r.returncode, 1)
        self.assertIn("read 1 of", r.stderr)
        self.assertFalse(any(c[0] == "omp" for c in self.calls()))

    def test_missing_local_list_refuses_openai_on_bet(self):
        self.bet_setup(["src/a.py"])
        r = self.run_bet([], "openai/gpt-6-astra:xhigh", PR_GATE_HDO_LOCAL=str(Path(self.tmp.name) / "nope"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("is missing", r.stderr)
        self.assertFalse(any(c[0] == "omp" for c in self.calls()))

    def test_pr_not_touching_pee_runs_openai_in_checkout_without_pee_dirs(self):
        sha = self.bet_setup(["tasks/a.py"])
        seen = Path(self.tmp.name) / "cwd.json"
        r = self.run_bet([end("VERDICT: APPROVE\nok", provider="openai", model="gpt-6-astra")],
                         "openai/gpt-6-astra:xhigh", STUB_CWD_LOG=str(seen))
        self.assertEqual(r.returncode, 0, r.stderr)
        got = json.loads(seen.read_text())
        checkout = self.out / "checkout"
        self.assertEqual(Path(got["cwd"]).resolve(), checkout.resolve())
        self.assertIn("tasks/a.py", got["files"])
        self.assertIn("experiments/2026-09-26--sami--proof-tree-screen/tasks.json", got["files"])
        self.assertFalse(any("pee-screen" in f or "sources/pee" in f or "tests/replay" in f or ".git" in f
                             for f in got["files"]), got["files"])
        omp = next(c for c in self.calls() if c[0] == "omp")
        self.assertEqual(omp[omp.index("--cwd") + 1], str(checkout))
        self.assertNotIn("task", omp[omp.index("--tools") + 1].split(","))
        self.assertIn(sha, omp[-1])
        self.assertIn("This run is isolated", omp[-1].splitlines()[0])
        self.assertIn("diff --git", (self.out / "tmp" / "pr-1.diff").read_text())
        self.assertEqual(len(self.st()["comments"]), 1)

    def test_omp_start_failure_on_unknown_tool_exits_7_naming_it(self):
        self.bet_setup(["tasks/a.py"])
        r = self.run_bet([end("VERDICT: APPROVE\nok", provider="openai", model="gpt-6-astra")],
                         "openai/gpt-6-astra:xhigh", STUB_UNAVAILABLE_TOOLS="grep")
        self.assertEqual(r.returncode, 7, r.stderr)
        self.assertIn("omp failed to start", r.stderr)
        self.assertIn("Built-in tools unavailable in this session: grep", r.stderr)
        self.assertNotIn("did not stay on", r.stderr)
        self.assertEqual(self.st()["comments"], [])

    def test_caller_cwd_holding_pee_path_refused(self):
        self.bet_setup(["tasks/a.py"])
        d = Path(self.tmp.name)
        held = d / "lane" / "tasks/proof-tasks/sources/pee"
        held.mkdir(parents=True)
        (held / "x.py").write_text("x")
        under_local = d / "pee-outputs" / "run1"
        under_local.mkdir(parents=True)
        hist = d / "hist"
        subprocess.run(["git", "clone", "-q", str(d / "bet-src"), str(hist)], check=True)
        subprocess.run(["git", "-C", str(hist), "rm", "-rq", "experiments", "tasks/proof-tasks", "tasks/tests"], check=True)
        subprocess.run(["git", "-C", str(hist), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
                        "commit", "-qm", "drop"], check=True)
        for cwd, needle in ((d / "lane", "tasks/proof-tasks/sources/pee"), (under_local, "pee-outputs"),
                            (hist, "in its git history")):
            r = self.run_bet([], "openai/gpt-6-astra:xhigh", "--cwd", str(cwd))
            self.assertEqual(r.returncode, 1, r.stderr)
            self.assertIn("refusing openai/gpt-6-astra:xhigh in --cwd", r.stderr)
            self.assertIn(needle, r.stderr)
        self.assertFalse(any(c[0] == "omp" for c in self.calls()))

    def test_events_read_outside_checkout_blocks_the_post(self):
        self.bet_setup(["tasks/a.py"])
        outside = Path(self.tmp.name) / "pee-outputs" / "t.jsonl"
        for call, needle in (({"toolName": "read", "args": {"path": str(outside)}}, "HDO-derived"),
                             ({"toolName": "bash", "args": {"command": "cat ../../bet-src/README.md"}}, "outside"),
                             ({"toolName": "read", "args": {"path": "history://abc"}}, "history://abc"),
                             ({"toolName": "read", "args": {"path": "xd://codegraph"}}, "xd://codegraph"),
                             ({"toolName": "bash", "args": {"command": "dispatch read BET-40"}}, "ran dispatch"),
                             ({"toolName": "bash", "args": {"command": "ls && gh pr view 911"}}, "ran gh"),
                             ({"toolName": "task", "args": {}}, "task ran")):
            r = self.run_bet([{"type": "tool_execution_start", **call},
                              end("VERDICT: APPROVE\nok", provider="openai", model="gpt-6-astra")],
                             "openai/gpt-6-astra:xhigh")
            self.assertEqual(r.returncode, 6, r.stderr)
            self.assertIn(needle, r.stderr)
        self.assertEqual(self.st()["comments"], [])
        ok = [{"type": "tool_execution_start", "toolName": "read", "args": {"path": "tasks/a.py:1-20"}},
              {"type": "tool_execution_start", "toolName": "bash", "args": {"command": "ls tasks && cat README.md"}},
              end("VERDICT: APPROVE\nok", provider="openai", model="gpt-6-astra")]
        self.assertEqual(self.run_bet(ok, "openai/gpt-6-astra:xhigh").returncode, 0)

    # --- merge ---

    def approved(self, sha=A):
        return [gate_comment("review", sha, "APPROVE", "2026-10-17T11:00:00Z", 1)]

    def assert_refused(self, r, *needles):
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        for n in needles:
            self.assertIn(n, r.stderr)
        self.assertFalse(self.st().get("merged"))
        self.assertFalse(any(c[:2] == ["pr", "merge"] for c in self.calls()))

    def test_merge_with_gate_at_head_and_green_ci(self):
        self.set_state(heads=[A], rollup=[check("gate")], comments=self.approved())
        r = self.merge()
        self.assertEqual(r.returncode, 0, r.stderr)
        merge = next(c for c in self.calls() if c[:2] == ["pr", "merge"])
        self.assertEqual(merge[merge.index("--match-head-commit") + 1], A)
        self.assertNotIn("--auto", merge)
        self.assertEqual(self.st()["merge_env"], "1")
        self.assertIn("c" * 40, r.stdout)

    def test_old_failure_superseded_by_success_merges(self):
        rollup = [check("gate", "FAILURE", "2026-10-17T09:00:00Z"), check("gate", "SUCCESS", "2026-10-17T10:00:00Z")]
        self.set_state(heads=[A], rollup=rollup, comments=self.approved())
        self.assertEqual(self.merge().returncode, 0)

    def test_old_success_superseded_by_failure_refuses(self):
        rollup = [check("gate", "SUCCESS", "2026-10-17T09:00:00Z"), check("gate", "FAILURE", "2026-10-17T10:00:00Z")]
        self.set_state(heads=[A], rollup=rollup, comments=self.approved())
        self.assert_refused(self.merge(), "CI/gate is FAILURE")

    def test_pending_check_refuses(self):
        self.set_state(heads=[A], rollup=[check("gate"), check("lint", None, status="IN_PROGRESS")],
                       comments=self.approved())
        self.assert_refused(self.merge(), "CI/lint is PENDING")

    def test_zero_checks_refuses_without_flag_and_merges_with_it(self):
        self.set_state(heads=[A], rollup=[], comments=self.approved())
        self.assert_refused(self.merge(), "no checks")
        self.assertEqual(self.merge("--allow-no-checks").returncode, 0)

    def test_gate_at_older_head_refuses_naming_it(self):
        self.set_state(heads=[B], rollup=[check("gate")], comments=self.approved(A))
        self.assert_refused(self.merge(), f"names {A}, not head {B}")

    def test_missing_gate_refuses(self):
        self.set_state(heads=[A], rollup=[check("gate")], comments=[])
        self.assert_refused(self.merge(), "gate review: no pr-gate comment")

    def test_request_changes_after_approve_at_same_head_refuses(self):
        comments = self.approved() + [gate_comment("review", A, "REQUEST_CHANGES", "2026-10-17T11:30:00Z", 2)]
        self.set_state(heads=[A], rollup=[check("gate")], comments=comments)
        self.assert_refused(self.merge(), "REQUEST_CHANGES")

    def test_approve_script_receives_head_and_gate_urls_instead_of_gh_merge(self):
        rec = Path(self.tmp.name) / "approve.log"
        script = Path(self.tmp.name) / "approve.sh"
        script.write_text(f"#!/bin/sh\necho \"$@\" > {rec}\ncat \"$3\" >> {rec}\necho PR_GATE_MERGE=$PR_GATE_MERGE >> {rec}\n"
                          f"python3 -c 'import json;p=\"{self.state}\";s=json.load(open(p));s[\"merged\"]=True;json.dump(s,open(p,\"w\"))'\n")
        script.chmod(0o755)
        self.set_state(heads=[A], rollup=[check("gate")], comments=self.approved())
        r = self.merge("--approve-script", str(script))
        self.assertEqual(r.returncode, 0, r.stderr)
        got = rec.read_text()
        self.assertTrue(got.startswith(f"1 {A} "))
        self.assertIn("https://x/1", got)
        self.assertFalse(any(c[:2] == ["pr", "merge"] for c in self.calls()))
        self.assertIn("PR_GATE_MERGE=1", got)

    # --- check ---

    def check(self, *extra):
        return self.pr_gate("check", "--repo", REPO, "--pr", "1", *extra)

    def test_check_require_any_passes_on_one_approving_gate_and_merges_nothing(self):
        comments = self.approved() + [gate_comment("other", B, "REQUEST_CHANGES", "2026-10-17T11:30:00Z", 2)]
        self.set_state(heads=[A], rollup=[check("gate")], comments=comments)
        r = self.check("--head", A, "--require-any")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("https://x/1", r.stdout)
        self.assertFalse(any(c[:2] == ["pr", "merge"] for c in self.calls()))

    def test_check_require_any_refuses_with_no_gate(self):
        self.set_state(heads=[A], rollup=[check("gate")], comments=[])
        self.assert_refused(self.check("--require-any"), "no gate's newest pr-gate comment approves", "no pr-gate comment")

    def test_check_require_any_refuses_a_gate_at_an_older_head(self):
        self.set_state(heads=[B], rollup=[check("gate")], comments=self.approved(A))
        self.assert_refused(self.check("--require-any"), f"gate review: newest comment names {A}")

    def test_check_require_any_refuses_request_changes_at_head_beside_an_approve(self):
        comments = self.approved() + [gate_comment("security", A, "REQUEST_CHANGES", "2026-10-17T11:30:00Z", 2)]
        self.set_state(heads=[A], rollup=[check("gate")], comments=comments)
        self.assert_refused(self.check("--require-any"), "gate security: newest comment at head is REQUEST_CHANGES")

    def test_check_require_any_refuses_request_changes_after_approve_on_the_same_gate(self):
        comments = self.approved() + [gate_comment("review", A, "REQUEST_CHANGES", "2026-10-17T11:30:00Z", 2)]
        self.set_state(heads=[A], rollup=[check("gate")], comments=comments)
        self.assert_refused(self.check("--require-any"), "REQUEST_CHANGES")

    def test_check_head_mismatch_refuses_naming_both(self):
        self.set_state(heads=[B], rollup=[check("gate")], comments=self.approved(B))
        self.assert_refused(self.check("--head", A, "--require-any"), A, B)

    def test_check_require_names_gates_and_ci_counts(self):
        self.set_state(heads=[A], rollup=[check("gate", "FAILURE")], comments=self.approved())
        self.assert_refused(self.check("--require", "review"), "CI/gate is FAILURE")
        self.set_state(heads=[A], rollup=[check("gate")], comments=self.approved())
        self.assertEqual(self.check("--require", "review").returncode, 0)
        self.assert_refused(self.check("--require", "review,security"), "gate security: no pr-gate comment")

    def test_check_require_and_require_any_are_exclusive(self):
        r = self.check("--require", "review", "--require-any")
        self.assertEqual(r.returncode, 2)

    def test_check_reads_a_merged_pr_and_merge_refuses_it(self):
        self.set_state(heads=[A], rollup=[check("gate")], comments=self.approved(), pr_state="MERGED")
        self.assertEqual(self.check("--head", A, "--require-any").returncode, 0)
        self.assert_refused(self.merge(), "is MERGED")


APPROVE_SCRIPT = Path.home() / ".worktrees/reports/review-app-approve.sh"


@unittest.skipUnless(APPROVE_SCRIPT.is_file(), f"{APPROVE_SCRIPT} is this machine's, not committed")
class ReviewAppApproveTest(unittest.TestCase):
    """review-app-approve.sh refuses before minting a token unless pr-gate check --require-any passes."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        (d / "gh").write_text(GH_STUB)
        (d / "secrets").write_text("#!/bin/sh\necho secrets \"$@\" >> \"$STUB_LOG\"\nexit 99\n")
        for name in ("gh", "secrets"):
            (d / name).chmod(0o755)
        self.state, self.log = d / "state.json", d / "calls.log"
        self.body = d / "body.md"
        self.body.write_text("Gates at head.\n")
        self.env = {**os.environ, "PATH": f"{d}:{os.environ['PATH']}", "STUB_STATE": str(self.state),
                    "STUB_LOG": str(self.log), "DOTFILES_DIR": str(GATE.parents[1])}

    def tearDown(self):
        self.tmp.cleanup()

    def approve(self, head, comments):
        self.state.write_text(json.dumps({"heads": [A], "rollup": [check("gate")], "comments": comments}))
        return subprocess.run([str(APPROVE_SCRIPT), "5", head, str(self.body), "subject"], env=self.env,
                              capture_output=True, text=True)

    def assert_refused_before_mint(self, r, needle):
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn(needle, r.stderr)
        log = self.log.read_text() if self.log.exists() else ""
        self.assertNotIn("secrets", log)
        self.assertNotIn('"merge"', log)

    def test_no_gate_at_head_refuses(self):
        self.assert_refused_before_mint(self.approve(A, []), "no pr-gate comment")

    def test_gate_at_an_older_head_refuses(self):
        self.assert_refused_before_mint(self.approve(A, [gate_comment("review", B, "APPROVE", "2026-10-17T11:00:00Z", 1)]),
                                        f"newest comment names {B}")

    def test_request_changes_at_head_refuses(self):
        comments = [gate_comment("review", A, "APPROVE", "2026-10-17T11:00:00Z", 1),
                    gate_comment("deep", A, "REQUEST_CHANGES", "2026-10-17T11:30:00Z", 2)]
        self.assert_refused_before_mint(self.approve(A, comments), "REQUEST_CHANGES")

    def test_a_head_other_than_the_prs_refuses(self):
        self.assert_refused_before_mint(self.approve(B, [gate_comment("review", B, "APPROVE", "2026-10-17T11:00:00Z", 1)]),
                                        f"heads at {A}, not {B}")

    def test_an_approving_gate_at_head_reaches_the_mint(self):
        r = self.approve(A, [gate_comment("review", A, "APPROVE", "2026-10-17T11:00:00Z", 1)])
        self.assertNotEqual(r.returncode, 0)  # the fake secrets exits 99: the script stops at the mint
        self.assertIn("secrets GH_REVIEW_APP_PRIVATE_KEY_B64", self.log.read_text())


if __name__ == "__main__":
    unittest.main()
