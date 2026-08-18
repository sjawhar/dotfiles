import { createClaudeBridge } from "@sjawhar/opencode-claude-bridge";
import os from "node:os";
import path from "node:path";

const DF = process.env.DOTFILES_DIR ?? path.join(os.homedir(), ".dotfiles");

export const DotfilesBridge = createClaudeBridge({
  sources: [
    { dir: path.join(DF, "plugins/sjawhar"), namespace: "sjawhar" },
    { dir: path.join(DF, "vendor/gh-stack"), namespace: "gh" },
    { dir: path.join(DF, "vendor/legion"), namespace: "legion" },
    { dir: path.join(DF, "vendor/pup"), namespace: "pup" },
    { dir: path.join(DF, "plugins/cursor-harvest"), namespace: "cursor-harvest" },
    // Skills disabled: sentry-for-ai ships ~25 per-platform SDK skills and Sami keeps
    // only sentry-python-sdk, which loads natively via the curated ~/.claude/skills/
    // sentry-for-ai dir (installers/opencode.sh). Bridge still handles commands/agents.
    { dir: path.join(DF, "vendor/sentry-for-ai"), namespace: "sentry", skills: false },
    { dir: path.join(DF, "vendor/sentry-cli/plugins/sentry-cli"), namespace: "sentry-cli" },
  ],
});
