// Export the OMP session identity into the process environment so child
// processes (bash tool, PTY) inherit it:
//
//   OMP_SESSION_ID — the resumable session id (`omp --resume <id>`), derived
//     from the session transcript filename, which is what resume matching
//     scans. Keys per-session state like pr-inbox's seen-state.
//   JJ_CONFIG — user config chain plus a generated per-session overlay that
//     sets `templates.commit_trailers`, so every jj commit made from an agent
//     session automatically carries an `Omp-Session: <id>` trailer. Attribution
//     rides the commit itself: any checkout can map a commit back to the
//     session that made it, with zero agent compliance required.
//
// Subagents load no extensions but share the parent process, and their
// session_start events fire process-wide. A session family deliberately
// shares one identity (one set of eyes): subagent transcripts live *inside*
// the top-level session's directory as <AgentName>.jsonl, so only transcripts
// with a <timestamp>_<uuid>.jsonl basename re-point the env. Session
// switch/branch re-points to the new top-level session.
//
// Attribution must degrade to *no* trailer, never a *wrong* one: stale
// overlays (from a parent OMP process or an earlier session) are stripped
// from the inherited chain, overlays are written atomically so a concurrent
// jj (e.g. the jj-snapshot hook) never reads a truncated layer, and a failed
// overlay write publishes the clean overlay-free base. Overlay files land in
// ~/.cache/omp/jj/ and are never cleaned up here: jj tolerates a missing
// path in JJ_CONFIG, and cache semantics make the directory safe to purge.
import { execFile } from "node:child_process";
import { mkdir, rename, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import path from "node:path";
import { promisify } from "node:util";
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";

const UUID = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";
const TOP_LEVEL_TRANSCRIPT = new RegExp(`^\\d{4}-\\d{2}-\\d{2}T[\\d-]+Z_(${UUID})\\.jsonl$`);
// Shared overlay filename grammar — the Legion extension emits the same shape
// so either implementation can recognize and strip the other's stale overlays.
const ATTRIBUTION_OVERLAY = /^omp-attribution-.*\.toml$/;
const OVERLAY_DIR = path.join(homedir(), ".cache", "omp", "jj");

function withoutOverlays(chain: string): string {
	return chain
		.split(":")
		.filter((entry) => entry && !ATTRIBUTION_OVERLAY.test(path.basename(entry)))
		.join(":");
}

// JJ_CONFIG replaces jj's default config lookup, so the overlay must chain
// the user layer explicitly. Honor a pre-existing JJ_CONFIG (captured before
// this extension ever overwrites it, minus any attribution overlays a parent
// process left in it); otherwise ask jj for its user config paths — chaining
// them in printed order reproduces the effective user config exactly
// (verified: `jj config list --user` is identical).
const inheritedJjConfig = process.env.JJ_CONFIG ? withoutOverlays(process.env.JJ_CONFIG) : "";
let basePromise: Promise<string | undefined> | undefined;

function jjConfigBase(): Promise<string | undefined> {
	basePromise ??= (async () => {
		if (inheritedJjConfig) return inheritedJjConfig;
		try {
			const { stdout } = await promisify(execFile)("jj", ["config", "path", "--user"]);
			const paths = stdout.split("\n").filter(Boolean);
			return paths.length > 0 ? paths.join(":") : undefined;
		} catch {
			return undefined; // no jj on this machine — OMP_SESSION_ID still exported
		}
	})();
	return basePromise;
}

export default function (pi: ExtensionAPI) {
	const set = async (
		_event: unknown,
		ctx: { sessionManager?: { getSessionFile?: () => string | undefined } },
	) => {
		const file = ctx.sessionManager?.getSessionFile?.();
		const id = file ? TOP_LEVEL_TRANSCRIPT.exec(path.basename(file))?.[1] : undefined;
		if (!id) return; // subagent or unpersisted session — keep the top-level identity
		process.env.OMP_SESSION_ID = id;
		const base = await jjConfigBase();
		if (!base) return;
		const overlay = path.join(OVERLAY_DIR, `omp-attribution-${id}.toml`);
		if (process.env.JJ_CONFIG?.split(":").includes(overlay)) return; // already active — never rewrite a live layer
		try {
			await mkdir(OVERLAY_DIR, { recursive: true });
			const tmp = `${overlay}.${process.pid}.tmp`;
			await writeFile(tmp, `[templates]\ncommit_trailers = '"Omp-Session: ${id}"'\n`);
			await rename(tmp, overlay);
			process.env.JJ_CONFIG = `${base}:${overlay}`;
		} catch {
			process.env.JJ_CONFIG = base; // degrade to no trailer, never a stale one
		}
	};
	pi.on("session_start", set);
	pi.on("session_switch", set);
	pi.on("session_branch", set);
}
