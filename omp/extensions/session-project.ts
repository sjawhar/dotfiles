// Make a session's model-spend project stick to it (dispatch://INFRA-165).
//
// scripts/agent-session tags every session's spend with a COS dashboard stream
// (theorem.project, and theorem.subproject when there is one, in
// OTEL_RESOURCE_ATTRIBUTES). On a resume it reads the project back from
// ~/.config/agent-spend/sessions/<session-id>, one line `project[ subproject]`,
// before it falls back to the working directory. This extension writes that
// file when a top-level session starts and has none yet, from the tags its
// process carries; agent-session itself rewrites it when a launch names
// THEOREM_PROJECT. No collector (OTEL_EXPORTER_OTLP_ENDPOINT unset) or no
// project tag: no file.
//
// Top-level transcripts only, the rule session-env.ts uses: subagents share the
// parent process, and their session_start events fire process-wide.
// The file is linked into place without overwriting, so a file agent-session
// wrote first wins. session_start awaits this, so it is bounded and never
// throws: a failure leaves the session without a file, and its resume falls
// back to the working directory.
import { link, mkdir, unlink, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import path from "node:path";
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";
import { TOP_LEVEL_TRANSCRIPT } from "./session-env";

const TIMEOUT_MS = 5_000;
const VALUE = /^[A-Za-z0-9._-]+$/;

/** The last theorem.project / theorem.subproject in OTEL_RESOURCE_ATTRIBUTES (later entries win). */
export function spendTags(attributes: string | undefined): { project?: string; subproject?: string } {
	const tags: { project?: string; subproject?: string } = {};
	for (const entry of (attributes ?? "").split(",")) {
		const eq = entry.indexOf("=");
		if (eq < 0) continue;
		const key = entry.slice(0, eq).trim();
		let value: string;
		try {
			value = decodeURIComponent(entry.slice(eq + 1).trim());
		} catch {
			continue;
		}
		if (key === "theorem.project") tags.project = value;
		else if (key === "theorem.subproject") tags.subproject = value;
	}
	return tags;
}

/**
 * Write <dir>/<id> for the session whose transcript is `file`, unless it exists.
 * Returns what it did, for the tests.
 */
export async function recordSessionProject(
	file: string | undefined,
	env: NodeJS.ProcessEnv = process.env,
	dir: string = path.join(env.XDG_CONFIG_HOME || path.join(homedir(), ".config"), "agent-spend", "sessions"),
): Promise<"written" | "exists" | "skipped" | "failed"> {
	const id = file ? TOP_LEVEL_TRANSCRIPT.exec(path.basename(file))?.[1] : undefined;
	if (!id || !env.OTEL_EXPORTER_OTLP_ENDPOINT) return "skipped";
	const { project, subproject } = spendTags(env.OTEL_RESOURCE_ATTRIBUTES);
	if (!project || !VALUE.test(project)) return "skipped";
	const line = subproject && VALUE.test(subproject) ? `${project} ${subproject}\n` : `${project}\n`;
	const target = path.join(dir, id);
	const tmp = path.join(dir, `.${id}.${process.pid}.tmp`);
	try {
		await mkdir(dir, { recursive: true, mode: 0o700 });
		await writeFile(tmp, line, { mode: 0o600 });
		try {
			await link(tmp, target);
			return "written";
		} catch (error) {
			return error instanceof Error && "code" in error && error.code === "EEXIST" ? "exists" : "failed";
		} finally {
			await unlink(tmp).catch(() => {});
		}
	} catch {
		return "failed";
	}
}

/** recordSessionProject, given up after `timeoutMs` so session_start never waits on a stuck filesystem. */
export async function recordSessionProjectBounded(
	file: string | undefined,
	timeoutMs = TIMEOUT_MS,
	env: NodeJS.ProcessEnv = process.env,
	dir?: string,
): Promise<"written" | "exists" | "skipped" | "failed" | "timeout"> {
	let timer: NodeJS.Timeout | undefined;
	const timeout = new Promise<"timeout">((resolve) => {
		timer = setTimeout(() => resolve("timeout"), timeoutMs);
		timer.unref?.();
	});
	try {
		return await Promise.race([recordSessionProject(file, env, dir).catch(() => "failed" as const), timeout]);
	} finally {
		clearTimeout(timer);
	}
}

export default function (pi: ExtensionAPI) {
	const record = async (
		_event: unknown,
		ctx: { sessionManager?: { getSessionFile?: () => string | undefined } },
	) => {
		try {
			await recordSessionProjectBounded(ctx.sessionManager?.getSessionFile?.());
		} catch {
			// never fail a session over its spend tag
		}
	};
	pi.on("session_start", record);
	pi.on("session_switch", record);
}
