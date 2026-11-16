// Runs session-env through a script of events in a fresh process, as the omp of an agent box runs
// it, and prints what the box's session file ($XDG_RUNTIME_DIR/agentbox-session) held after each
// step. The extension reads AGENTBOX_RUNNER_PID and its own parent when it loads, so each script
// is its own process. argv[2]: JSON `{ managers, steps }`.
//   managers: { <name>: { id?, file? } }, the session managers the events carry
//   steps, each one of:
//     { event, agent: { kind, id }, manager }  one handler call, with that session manager in ctx
//     { set: <name>, id?, file? }               a change no event reports: omp's silent change of
//                                               id, or a /move of the transcript
//     { sleep: <ms> }                           time for the extension's timer
//     { hold: "open" | "read" }                 open the session file and keep the descriptor, or
//                                               read the whole file through the descriptor held
//                                               since `open` and close it. A rename puts a NEW
//                                               file in place, so a descriptor opened before a
//                                               write still reads the whole file as it was; a
//                                               write in place reads through to the new bytes, and
//                                               after the truncation to none. `holds` has each
//                                               read, in order.
// Each session manager's ensureOnDisk records its session's id and what the session file held
// when it was called.
import { closeSync, openSync, readFileSync, readSync, statSync } from "node:fs";
import path from "node:path";
import sessionEnv from "../session-env";

type Handler = (event: unknown, ctx: unknown) => Promise<unknown>;
type State = { id?: string; file?: string };
type Step =
	| { event: string; agent: { kind: "main" | "sub"; id: string }; manager: string }
	| ({ set: string } & State)
	| { sleep: number }
	| { hold: "open" | "read" };

const script = JSON.parse(process.argv[2] ?? "{}") as { managers: Record<string, State>; steps: Step[] };
const sessionFile = path.join(process.env.XDG_RUNTIME_DIR ?? "", "agentbox-session");
const held = (): { content: string; ino: number } | null => {
	try {
		return { content: readFileSync(sessionFile, "utf8"), ino: statSync(sessionFile).ino };
	} catch {
		return null;
	}
};

const holds: (string | null)[] = [];
let heldFd: number | undefined;
// The whole of what the held descriptor points at, from its start, however the file has changed.
const readHeld = (fd: number): string => {
	const chunks: Buffer[] = [];
	const buffer = Buffer.alloc(65536);
	for (let at = 0; ; ) {
		const read = readSync(fd, buffer, 0, buffer.length, at);
		if (read <= 0) break;
		chunks.push(Buffer.from(buffer.subarray(0, read)));
		at += read;
	}
	return Buffer.concat(chunks).toString("utf8");
};

const saved: { manager: string; id: string | undefined; held: string | null }[] = [];
const states = new Map<string, State>();
const managers = new Map<string, unknown>();
for (const [name, initial] of Object.entries(script.managers)) {
	const state: State = { ...initial };
	states.set(name, state);
	managers.set(name, {
		getSessionId: () => state.id,
		getSessionFile: () => state.file,
		ensureOnDisk: async () => {
			saved.push({ manager: name, id: state.id, held: held()?.content ?? null });
		},
	});
}

const handlers = new Map<string, Handler>();
sessionEnv({ on: (event: string, handler: Handler) => handlers.set(event, handler) } as never);
const snapshots: ({ content: string; ino: number } | null)[] = [];
for (const step of script.steps) {
	if ("event" in step) {
		await handlers.get(step.event)?.({ type: step.event }, {
			agent: { ...step.agent, name: step.agent.id.toLowerCase(), depth: 0 },
			hasUI: false,
			ui: { notify: () => {} },
			sessionManager: managers.get(step.manager),
		});
	} else if ("set" in step) {
		const state = states.get(step.set) as State;
		state.id = step.id;
		state.file = step.file;
	} else if ("hold" in step) {
		if (step.hold === "open") {
			heldFd = openSync(sessionFile, "r");
		} else if (heldFd !== undefined) {
			holds.push(readHeld(heldFd));
			closeSync(heldFd);
			heldFd = undefined;
		} else {
			holds.push(null);
		}
	} else {
		await Bun.sleep(step.sleep);
	}
	snapshots.push(held());
}
process.stdout.write(JSON.stringify({ pid: process.pid, snapshots, saved, holds }));
