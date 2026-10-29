// served-model-notice e2e harness (run.sh): one PASS or FAIL line per case, from the worker's
// transcript (its served-model-notice messages and served-by-other-model entries) and the request
// bodies the stub saw. Usage: bun check.ts <work dir>. Exits 1 when any case fails.
import * as fs from "node:fs";
import * as path from "node:path";

const work = process.argv[2];
if (!work) throw new Error("usage: bun check.ts <work dir>");

type Entry = { type: string; customType?: string; details?: Record<string, unknown>; data?: Record<string, unknown> };
type Request = { file: string; model: string; text: string };
type Notice = Record<string, unknown>;
/** One case: the worker's transcript and the requests it sent. */
interface Run {
	worker: string | undefined;
	notices: Notice[];
	recorded: Notice[];
	compactions: number;
	requests: Request[];
	summaries: number;
}

function walk(dir: string): string[] {
	return fs.readdirSync(dir, { withFileTypes: true }).flatMap(d => (d.isDirectory() ? walk(path.join(dir, d.name)) : [path.join(dir, d.name)]));
}

function load(name: string): Run {
	const run = path.join(work, name);
	const transcripts = walk(path.join(run, "agent", "sessions")).filter(f => f.endsWith(".jsonl"));
	// A root transcript is <timestamp>_<id>.jsonl; a task subagent's is <name>.jsonl under its root's directory.
	const rootFile = /^\d{4}-\d\d-\d\dT.*_.*\.jsonl$/;
	const worker = transcripts.find(f => (name.endsWith("-sub") ? path.basename(f) === "Probe.jsonl" : rootFile.test(path.basename(f))));
	const entries: Entry[] = worker ? Bun.JSONL.parse(fs.readFileSync(worker, "utf8")) as Entry[] : [];
	const role = name.endsWith("-sub") ? "-sub-" : "-top-";
	const requests: Request[] = fs
		.readdirSync(path.join(run, "req"))
		.sort()
		.filter(f => f.includes(role) && !f.endsWith("-429.json"))
		.map(f => ({ file: f, model: f.replace(/^req-\d+-(sub|top)-/, "").replace(/\.json$/, ""), text: fs.readFileSync(path.join(run, "req", f), "utf8") }))
		// The session's own turns carry tools; the title generator's request, which also quotes the marker, has none.
		.filter(q => (JSON.parse(q.text) as { tools?: unknown[] }).tools?.length);
	return {
		worker,
		notices: entries.filter(e => e.type === "custom_message" && e.customType === "served-model-notice").map(e => e.details ?? {}),
		recorded: entries.filter(e => e.type === "custom" && e.customType === "served-by-other-model").map(e => e.data ?? {}),
		compactions: entries.filter(e => e.type === "compaction").length,
		requests,
		summaries: fs.readdirSync(path.join(run, "req")).filter(f => f.includes("-summary-")).length,
	};
}

const count = (text: string, needle: string) => text.split(needle).length - 1;
const SUB = "Model substitution:";
let failed = 0;

function check(name: string, test: (r: Run) => string[]) {
	let problems: string[];
	try {
		const r = load(name);
		problems = r.worker ? test(r) : ["no worker transcript"];
		const kinds = r.notices.map(n => `${n.kind}${n.placement ? `/${n.placement}` : ""}`).join(", ") || "none";
		console.log(`${problems.length ? "FAIL" : "PASS"} ${name}: notices [${kinds}], recorded ${r.recorded.length}, compactions ${r.compactions}${problems.length ? ` -- ${problems.join("; ")}` : ""}`);
	} catch (err) {
		problems = [String(err)];
		console.log(`FAIL ${name}: ${err}`);
	}
	if (problems.length) failed++;
}

const expect = (ok: boolean, what: string) => (ok ? [] : [what]);
const chainOnce = (r: Run) => {
	const first = r.requests.find(q => q.model === "claude-opus-5");
	return [
		...expect(r.notices.length === 1 && r.notices[0].kind === "chain", "want exactly one chain notice"),
		...expect(String(r.notices[0]?.asked).startsWith("anthropic/claude-opus-5-5") && r.notices[0]?.served === "anthropic/claude-opus-5", "want asked opus-5-5, served opus-5"),
		...expect(r.notices[0]?.placement === "top", "want placement top"),
		...expect(!!first && count(first.text, SUB) === 1, "want the notice in the first request opus-5 answered"),
		...expect(r.requests.filter(q => q.model === "claude-opus-5-5").every(q => count(q.text, SUB) === 0), "no notice before the fallback"),
		...expect(r.recorded.length === 0, "a chain fallback is not an Anthropic substitution"),
	];
};

for (const name of ["chain-top", "chain-sub"]) check(name, chainOnce);

check("deep-sub", r => {
	const first = r.requests.find(q => q.model === "claude-opus-4-7");
	return [
		...expect(r.notices.map(n => n.placement).join() === "top,top,below", "want placements top, top, below"),
		...expect(r.notices.every(n => String(n.asked).startsWith("anthropic/claude-opus-5-5")), "every step names the original model"),
		...expect(!!first && first.text.includes("MODEL BELOW FLOOR: asked anthropic/claude-opus-5-5"), "want the below-floor line in the first opus-4-7 request"),
	];
});

for (const name of ["none-top", "none-sub"]) {
	check(name, r => [
		...expect(r.notices.length === 0 && r.recorded.length === 0, "want nothing"),
		...expect(r.requests.length > 0 && r.requests.every(q => count(q.text, SUB) === 0), "no request carries a notice"),
	]);
}

check("inreq-sub", r => [
	...expect(r.notices.length === 1 && r.notices[0].kind === "anthropic", "want exactly one anthropic notice"),
	...expect(r.notices[0]?.asked === "anthropic/claude-fable-5-1" && r.notices[0]?.served === "anthropic/claude-opus-5", "want asked fable-5-1, served opus-5"),
	...expect(r.recorded.length === 1 && r.recorded[0].handoff === "claude-fable-5-1 -> claude-opus-5", "want one recorded handoff"),
	...expect(count(r.requests[0]?.text ?? "", SUB) === 0 && r.requests.slice(1).every(q => count(q.text, SUB) === 1), "want the notice in every request after the substituted turn"),
]);

for (const name of ["compact-top", "compact-sub"]) {
	check(name, r => {
		const after = r.requests.at(-1);
		return [
			...expect(r.compactions >= 1 && r.summaries >= 1, "want a committed compaction"),
			...expect(r.notices.map(n => n.kind).join() === "chain,resent-after-compaction", "want the notice, then one re-send"),
			...expect(!!after && count(after.text, SUB) === 1, "want the notice exactly once in the last request after the compaction"),
		];
	});
}

for (const name of ["restore-top", "restore-sub"]) {
	check(name, r => [
		...expect(r.notices.map(n => n.kind).join() === "chain,restored", "want the chain notice, then one restored notice"),
		...expect(r.recorded.length === 0, "the turn that finished on the old model after the switch is not an Anthropic substitution"),
		...expect(r.requests.some(q => q.text.includes("Model restored:")), "want the restored notice in a later request"),
	]);
}

// The chain entry is `anthropic/claude-opus-5:low`. With a 1 ms cooldown refusal-cooldown restores from
// the `context` hook of the first request after the fallback, so that request (still to opus-5) already
// carries the restored effort; without the effort restore every request from it on ran at `low`.
check("restore-top", r => {
	const effort = (q: Request | undefined) => (q ? (JSON.parse(q.text) as { output_config?: { effort?: string } }).output_config?.effort : undefined);
	const restored = r.requests.filter(q => q.model === "claude-opus-5-5");
	console.log(`  restore-top: effort after the restore ${restored.map(effort).join(", ")}`);
	return expect(restored.length > 0 && restored.every(q => effort(q) === "xhigh"), "want the session's configured xhigh back after the restore");
});

check("startup-sub", r => {
	const first = r.requests[0];
	const modelLine = first ? /- Model: ([^\\"]+)/.exec(first.text)?.[1] : undefined;
	console.log(`  startup-sub: the subagent's system prompt says "Model: ${modelLine}"`);
	return [
		...expect(r.notices.length === 1 && r.notices[0].kind === "startup", "want exactly one startup notice"),
		...expect(r.notices[0]?.asked === "openai-codex/gpt-5.6-terra" && r.notices[0]?.served === "anthropic/claude-opus-5", "want asked gpt-5.6-terra, served opus-5"),
		...expect(!!first && count(first.text, SUB) === 1, "want the notice in the subagent's first request"),
	];
});

process.exit(failed ? 1 : 0);
