// Tells a session, top-level or subagent, when a model other than the one it was asked to run is
// serving it: omp's own fallback chain, or Anthropic's in-request fallback. What it does and why is
// in served-model-notice-core.ts; the words the model reads are in served-model-notice.md.
//
// This file only binds the core to omp: the configured chains come from omp's own settings handle,
// imported from the binary's bundled copy (the bash gate's pattern), because `ctx` exposes no
// settings. The installer links only this file; the core resolves from the link's real path.
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";
import { cfgRetryFallbackChains } from "@oh-my-pi/pi-coding-agent/session/settings";
import { bindServedModelNotice } from "./served-model-notice-core";

export default function (pi: ExtensionAPI) {
	bindServedModelNotice(
		pi,
		() => cfgRetryFallbackChains.get(pi.pi.settings),
		role => pi.pi.settings.getModelRole(role),
	);
}
