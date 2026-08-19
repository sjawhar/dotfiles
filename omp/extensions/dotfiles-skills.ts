// Feed dotfiles skill pools to OMP via the resources_discover extension event.
// Reads the shared manifest (skills-sources.json) that also drives the OpenCode
// dotfiles-bridge, so adding a pool in one place reaches both harnesses.
// Requires an OMP build that emits resources_discover (v17.3.8-sami.20260820+).
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";

const DOTFILES_DIR = process.env.DOTFILES_DIR ?? path.join(os.homedir(), ".dotfiles");
const MANIFEST_PATH = path.join(DOTFILES_DIR, "skills-sources.json");

interface SkillSource {
	root: string;
	namespace: string;
	skills?: boolean;
	omp?: boolean;
	ompSkillsDir?: string;
}

function expandHome(value: string): string {
	return value.startsWith("~/") ? path.join(os.homedir(), value.slice(2)) : value;
}

function skillDirsFromManifest(): string[] {
	const manifest = JSON.parse(fs.readFileSync(MANIFEST_PATH, "utf8")) as { sources: SkillSource[] };
	const dirs: string[] = [];
	for (const source of manifest.sources) {
		if (source.omp === false) continue;
		const dir = source.ompSkillsDir
			? expandHome(source.ompSkillsDir)
			: path.join(DOTFILES_DIR, source.root, "skills");
		// Vendor checkouts are machine-dependent (ensure_vendor); absent ones are
		// expected on partially provisioned machines, not an error.
		if (fs.existsSync(dir)) dirs.push(dir);
	}
	return dirs;
}

export default function (pi: ExtensionAPI) {
	pi.on("resources_discover", async () => ({ skillPaths: skillDirsFromManifest() }));
}
