"""Label every Sami Dispatch event (ask answers and comments) with the failure codebook.

Each Dispatch ask is an agent's output and Sami's answer is a human label on it, so the
per-label daily rate is a standing measure of how agents ask. It calls a strong model
through the omp eval kernel's `completion()` helper, so it is loaded into an eval Python
cell rather than run as a subprocess:

    %load ~/.dotfiles/plugins/sjawhar/skills/reflect/classify-sami-events.py
    await main("dispatch-human.jsonl", "labels.jsonl")

Input is `extract-dispatch-human.py --out` output. Events the model fails to label are
written with `"labels": null` and counted on stdout; rerun those, never read them as
neutral. DN is the 1-based index of the event in created_at order.
"""
import asyncio
import json
import re
from pathlib import Path

LABELS = [
    "needless_ask", "already_answered", "unreadable", "wrong_claim",
    "invented_scope", "untested", "reinvented", "substantive", "neutral",
]

CODEBOOK = """
needless_ask: the agent could plausibly have done this itself (it already holds the
  authority/credential/access named or implied, or a standing rule already covers it,
  e.g. "the queue organizer merges"), so asking wastes Sami's time.
already_answered: this is a duplicate ask, a relitigation of a decision Sami already
  made, or something already approved/discussed — Sami's answer says so or implies it.
unreadable: the ask/comment uses an undefined coined term, is too vague to act on, is a
  wall of text, bundles multiple unrelated questions, or references something by a bare
  ID/hash with no link/context.
wrong_claim: the agent asserted something false or unsourced as fact, including a wrong
  premise about how our systems, data or earlier decisions work (a fabricated cause, a
  stale fact, a claim Sami corrects with "No it doesn't" / "Says who?" / "False").
invented_scope: the agent proposed/added an unrequested requirement, security theater,
  a special case, a second code path, or otherwise expanded scope beyond what was asked.
untested: the agent shipped, claimed, or is asking approval for something that was
  never actually tested/run, or whose correctness can "only be proven in production."
reinvented: the agent built something from scratch where a standard/existing
  solution/tool/package was available and should have been used/checked first.
substantive: genuine design input or a real decision that is not itself a symptom of
  any of the above failure classes — a legitimate judgment call for Sami to make.
neutral: pure data, an acknowledgment, "Done", a pasted value, or an accepted
  recommendation with no sign of a failure class in the ask itself.
""".strip()

SYSTEM = f"""You are labeling a corpus of Dispatch events: each is either an agent's
question to Sami (an "ask") with Sami's chosen answer, or a comment Sami wrote (which
may reply to an ask). Sami is an experienced, impatient engineering lead running a fleet
of 30-60 coding agents; he corrects agents constantly. Your job is to read the ask/
comment content and Sami's response, and infer whether the INTERACTION ITSELF shows one
or more of these failure classes in the agent's behavior (not Sami's behavior).

Codebook (multi-label — apply every label that fits, output at least one label):
{CODEBOOK}

Rules:
- A pick-only answer that simply accepts the agent's stated recommendation, with no
  sign of annoyance or correction, is `neutral` unless the ask's own content already
  shows a failure class (e.g. the ask itself was unreadable jargon even though Sami
  picked an option anyway).
- Look for Sami's own tells: "already answered"/"I said this" -> already_answered;
  "why are you asking me, you have access/admin" -> needless_ask; "what is X", "I don't
  know what that means" -> unreadable; "that's false/you're wrong/no it doesn't" ->
  wrong_claim; "I didn't ask for that/stop adding this" -> invented_scope; "did you even
  test this/you never ran it" -> untested; "we already have X/why reinvent" ->
  reinvented.
- If Sami's answer reads as a genuine considered decision with no corrective tone, use
  substantive.
- Multiple labels are fine and expected when more than one failure is present.
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "dn": {"type": "integer"},
                    "labels": {
                        "type": "array",
                        "items": {"type": "string", "enum": LABELS},
                        "minItems": 1,
                    },
                },
                "required": ["dn", "labels"],
            },
        },
    },
    "required": ["results"],
}


def get_text(e):
    if e["type"] == "ask.answered":
        return e["payload"]["answer"].get("text")
    elif e["type"] in ("comment.created", "comment.edited"):
        return e["payload"].get("body")
    return None


def load_sami_events(path):
    events = []
    with open(path) as f:
        for line in f:
            e = json.loads(line)
            if e["actor"]["id"].startswith("sami@"):
                events.append(e)
    events.sort(key=lambda e: e["created_at"])
    return events


def build_item(dn, e):
    t = e["type"]
    p = e["payload"]
    if t == "ask.answered":
        q = p.get("question") or ""
        options = [o.get("label", "") for o in (p.get("options") or [])]
        ans = p["answer"]
        return {
            "dn": dn, "issue": e["issue_key"], "created_at": e["created_at"],
            "kind": "ask",
            "question": q[:1200],
            "options": options,
            "selected": ans.get("selected") or [],
            "answer_text": (ans.get("text") or "")[:1200],
        }
    else:
        anchor = p.get("anchor") or {}
        return {
            "dn": dn, "issue": e["issue_key"], "created_at": e["created_at"],
            "kind": "comment",
            "anchor_quote": (anchor.get("quote") or "")[:500] if anchor else "",
            "ask_question": (p.get("ask_question") or "")[:800],
            "comment_body": (p.get("body") or "")[:1200],
        }


def render_item(item):
    lines = [f"--- DN {item['dn']} ({item['kind']}, {item['issue']}, {item['created_at']})"]
    if item["kind"] == "ask":
        lines.append(f"QUESTION: {item['question']}")
        if item["options"]:
            lines.append(f"OPTIONS: {item['options']}")
        if item["selected"]:
            lines.append(f"SAMI SELECTED: {item['selected']}")
        if item["answer_text"]:
            lines.append(f"SAMI SAID: {item['answer_text']}")
    else:
        if item["ask_question"]:
            lines.append(f"REPLYING TO ASK: {item['ask_question']}")
        if item["anchor_quote"]:
            lines.append(f"QUOTED TEXT: {item['anchor_quote']}")
        lines.append(f"SAMI'S COMMENT: {item['comment_body']}")
    return "\n".join(lines)


def batch_prompt(batch_items):
    rendered = "\n\n".join(render_item(it) for it in batch_items)
    return (
        f"Label each of the following {len(batch_items)} Dispatch events. "
        f"Return one result per DN, in the `results` array, in the schema given.\n\n"
        f"{rendered}"
    )


async def classify_batch(batch_items, model="default", retries=2):
    prompt = batch_prompt(batch_items)
    by_dn = {}
    for _ in range(retries + 1):
        h = completion(prompt, model=model, system=SYSTEM, schema=SCHEMA)
        try:
            raw = await asyncio.wait_for(asyncio.to_thread(h.wait), timeout=180)
        except asyncio.TimeoutError:
            continue
        data = json.loads(raw) if isinstance(raw, str) else raw
        by_dn.update({r["dn"]: r["labels"] for r in data["results"]})
        if all(it["dn"] in by_dn for it in batch_items):
            break
    return by_dn


async def classify_all(items, batch_size=12, concurrency=8, model="default"):
    batches = [items[i:i + batch_size] for i in range(0, len(items), batch_size)]
    sem = asyncio.Semaphore(concurrency)
    results = {}

    async def run_one(b):
        async with sem:
            r = await classify_batch(b, model=model)
            results.update(r)

    await asyncio.gather(*(run_one(b) for b in batches))
    return results


async def main(dispatch_jsonl_path, out_labels_path, model="default"):
    events = load_sami_events(dispatch_jsonl_path)
    items = [build_item(i + 1, e) for i, e in enumerate(events)]
    by_dn = await classify_all(items, model=model)
    out = Path(out_labels_path)
    with out.open("w") as f:
        for it, e in zip(items, events):
            rec = {
                "dn": it["dn"],
                "issue": it["issue"],
                "project": e.get("project"),
                "created_at": it["created_at"],
                "kind": it["kind"],
                "labels": by_dn.get(it["dn"]),
                "asking_session": (e["payload"].get("author") or {}).get("origin"),
            }
            f.write(json.dumps(rec) + "\n")
    unlabeled = sum(1 for it in items if it["dn"] not in by_dn)
    print(f"Wrote {len(items)} events to {out}; {unlabeled} unlabeled (labels null)")
    return out
