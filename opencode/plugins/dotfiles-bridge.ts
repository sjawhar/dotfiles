import { createClaudeBridge } from "@sjawhar/opencode-claude-bridge";
import os from "node:os";
import path from "node:path";

const DF = process.env.DOTFILES_DIR ?? path.join(os.homedir(), ".dotfiles");

export const DotfilesBridge = createClaudeBridge({
  sources: [
    { dir: path.join(DF, "plugins/sjawhar"), namespace: "sjawhar" },
    { dir: path.join(DF, "vendor/legion/.opencode"), namespace: "legion" },
    { dir: path.join(DF, "vendor/sentry-for-ai"), namespace: "sentry" },
    { dir: path.join(DF, "vendor/ghost-wispr/.opencode"), namespace: "wispr" },
  ],
});
