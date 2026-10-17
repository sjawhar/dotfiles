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
if args[0] == "api" and "/pulls/" in args[1]:
    print(head())
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
    if "statusCheckRollup" in args[args.index("--json") + 1]:
        print(json.dumps({"statusCheckRollup": st["rollup"], "headRefOid": st["heads"][0], "state": "OPEN"}))
    else:
        print(json.dumps({"state": "MERGED" if st.get("merged") else "OPEN", "mergeCommit": {"oid": "c" * 40} if st.get("merged") else None}))
elif args[:2] == ["pr", "merge"]:
    st["merged"] = True; save()
else:
    sys.exit(f"unexpected gh {args}")
'''

# Emits $STUB_EVENTS (a JSON list of events) as JSONL; with $STUB_MOVE_HEAD set, the head moves.
OMP_STUB = r'''#!/usr/bin/env python3
import json, os, sys
open(os.environ["STUB_LOG"], "a").write(json.dumps(["omp", *sys.argv[1:]]) + "\n")
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
        self.assertIn("$0.75", c["body"])
        self.assertIn("Looks right.", c["body"])
        self.assertIn(f"<!-- pr-gate gate=review head={A} verdict=APPROVE", c["body"])
        omp = next(c for c in self.calls() if c[0] == "omp")
        self.assertIn(A, omp[-1].splitlines()[0])
        self.assertEqual(omp[omp.index("--model") + 1], "anthropic/claude-opus-5-5:xhigh")

    def test_head_moved_mid_run_posts_nothing_and_names_both(self):
        r = self.run_gate([end("VERDICT: APPROVE\nok")], STUB_MOVE_HEAD=B)
        self.assertEqual(r.returncode, 5)
        self.assertIn(A, r.stderr)
        self.assertIn(B, r.stderr)
        self.assertEqual(self.st()["comments"], [])

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

    def test_openai_model_refused_before_any_run(self):
        r = self.run_gate([end("VERDICT: APPROVE")], "--model", "openai/gpt-6-astra:xhigh")
        self.assertEqual(r.returncode, 1)
        self.assertIn("OpenAI spend is paused", r.stderr)
        self.assertFalse(any(c[0] == "omp" for c in self.calls()))

    def test_legion_without_checkout_refused_before_any_run(self):
        r = self.pr_gate("run", "--repo", "sjawhar/legion", "--pr", "1", "--gate", "review", "--brief",
                         str(self.brief), STUB_EVENTS="[]", PR_GATE_LEGION_CHECKOUT="")
        self.assertEqual(r.returncode, 1)
        self.assertIn("--legion-checkout", r.stderr)
        self.assertFalse(any(c[0] == "omp" for c in self.calls()))

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
        script.write_text(f"#!/bin/sh\necho \"$@\" > {rec}\ncat \"$3\" >> {rec}\n"
                          f"python3 -c 'import json;p=\"{self.state}\";s=json.load(open(p));s[\"merged\"]=True;json.dump(s,open(p,\"w\"))'\n")
        script.chmod(0o755)
        self.set_state(heads=[A], rollup=[check("gate")], comments=self.approved())
        r = self.merge("--approve-script", str(script))
        self.assertEqual(r.returncode, 0, r.stderr)
        got = rec.read_text()
        self.assertTrue(got.startswith(f"1 {A} "))
        self.assertIn("https://x/1", got)
        self.assertFalse(any(c[:2] == ["pr", "merge"] for c in self.calls()))


if __name__ == "__main__":
    unittest.main()
