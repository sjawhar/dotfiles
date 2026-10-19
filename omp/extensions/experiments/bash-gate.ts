// Binds omp's own interceptor pieces into the bash gate (bash-gate-core.ts): the rules omp would
// apply (`bashInterceptor.patterns`, its defaults unless configured, read from the session's
// settings through `pi.pi`, omp's own root module), checkBashInterception, and omp's shell segment
// and leading-`cd` parsers. The handler runs on every bash `tool_call`, in top-level sessions and
// subagents alike, in both arms of the experiments extension's `bash_gate_leading` gate
// (index.ts), which says which arm this session drew. Binding turns omp's built-in interceptor off
// for this process only, as a runtime override on omp's settings (in memory, never written to
// disk), so this is the only interceptor here while the shared config.yml keeps it on for
// processes without this extension. The root session reads that same Settings instance, and a
// `task` subagent's settings are an overlay that reads through to its parent's, so both see the override; the rules
// are read the same way, regardless of `enabled`.
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";
import { cfgBashInterceptorEnabled, cfgBashInterceptorPatterns } from "@oh-my-pi/pi-coding-agent/exec/settings";
import { checkBashInterception } from "@oh-my-pi/pi-coding-agent/tools/bash-interceptor";
import { extractFlatShellCommandSegments, extractLeadingCdTarget } from "@oh-my-pi/pi-coding-agent/tools/shell-tokenize";
import { type Interceptor, intercept } from "./bash-gate-core";

const OMP: Interceptor = { check: checkBashInterception, segments: extractFlatShellCommandSegments, leadingCd: extractLeadingCdTarget };

type CallEvent = { toolName: string; input: Record<string, unknown> };

export default function bashGate(pi: ExtensionAPI, leadingOnly: () => boolean): void {
	cfgBashInterceptorEnabled.override(pi.pi.settings, false);
	pi.on("tool_call", (event: CallEvent) => {
		if (event.toolName !== "bash") return undefined;
		const reason = intercept(OMP, event.input, cfgBashInterceptorPatterns.get(pi.pi.settings), pi.getActiveTools(), leadingOnly());
		return reason === undefined ? undefined : { block: true, reason };
	});
}
