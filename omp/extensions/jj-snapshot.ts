// Port of dotfiles jj-snapshot.ts: snapshot working copy after mutating tools.
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";

export default function (pi: ExtensionAPI) {
	pi.on("tool_result", async (event) => {
		if (!/^(edit|write|bash|multiedit|apply_patch)$/.test(event.toolName)) return;
		try {
			await Bun.$`jj root && jj st`.quiet().nothrow();
		} catch {
			// not a jj repo; nothing to snapshot
		}
	});
}
