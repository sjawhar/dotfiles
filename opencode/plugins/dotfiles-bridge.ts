import { createClaudeBridge } from "@sjawhar/opencode-claude-bridge";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const DF = process.env.DOTFILES_DIR ?? path.join(os.homedir(), ".dotfiles");

// Shared manifest also consumed by omp/extensions/dotfiles-skills.ts (OMP side),
// so a pool added there reaches both harnesses. See the manifest's $comment for
// field semantics.
interface SkillSource {
  root: string;
  namespace: string;
  skills?: boolean;
  omp?: boolean;
  ompSkillsDir?: string;
}

const manifest = JSON.parse(
  fs.readFileSync(path.join(DF, "skills-sources.json"), "utf8"),
) as { sources: SkillSource[] };

export const DotfilesBridge = createClaudeBridge({
  sources: manifest.sources.map((source) => ({
    dir: path.join(DF, source.root),
    namespace: source.namespace,
    ...(source.skills === false && { skills: false }),
  })),
});
