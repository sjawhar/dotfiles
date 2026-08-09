/** @jsxImportSource @opentui/solid */
import type { TuiPlugin, TuiPluginModule } from "@opencode-ai/plugin/tui";
import { execFile } from "node:child_process";
import { createSignal } from "solid-js";
import { promisify } from "node:util";

// Shows the GitHub PR covering the current change, e.g. "METR/hawk #1085".
//
// jj leaves git in detached HEAD, so `git branch --show-current` is empty or
// points at the wrong commit. The bookmark is found by walking ancestors, which
// covers both layouts: bookmark on @ and bookmark on @- (a fresh empty child).
//
// A PR opened from a fork lives in the *base* repo, so base is queried before
// origin. Suppressed on the default branch, matching Claude Code's footer.

const REVSET = "heads(::@ & bookmarks())";
const TEMPLATE = 'bookmarks.map(|b| b.name()).join("\n")';
const POLL_MS = 4_000;
const STALE_MS = 60_000;

type Pr = { number: number; draft: boolean; repo: string };

const exec = promisify(execFile);
const run = async (cmd: string, args: string[], cwd: string) => {
  try {
    return (await exec(cmd, args, { cwd, timeout: 10_000 })).stdout.trim();
  } catch {
    return "";
  }
};

const isJj = new Map<string, boolean>();
const repoMeta = new Map<string, { def: string; repos: string[] }>();

async function currentBranch(cwd: string) {
  let jj = isJj.get(cwd);
  if (jj === undefined) {
    jj = !!(await run("jj", ["root"], cwd));
    isJj.set(cwd, jj);
  }
  if (!jj) return run("git", ["branch", "--show-current"], cwd);
  const out = await run("jj", ["log", "-r", REVSET, "--no-graph", "-T", TEMPLATE], cwd);
  return out.split("\n")[0] ?? "";
}

async function resolvePr(cwd: string, branch: string): Promise<Pr | null> {
  const origin = (await run("git", ["remote", "get-url", "origin"], cwd))
    .replace(/\.git$/, "")
    .replace(/^.*github\.com[:/]/, "");
  if (!origin) return null;

  let meta = repoMeta.get(origin);
  if (!meta) {
    const raw = await run("gh", ["api", `repos/${origin}`], cwd);
    if (!raw) return null;
    const json = JSON.parse(raw);
    meta = {
      def: json.default_branch ?? "",
      // base repo first: a fork's PR lives upstream, not in the fork.
      repos: [...new Set([json.parent?.full_name, json.source?.full_name, origin].filter(Boolean))],
    };
    repoMeta.set(origin, meta);
  }
  if (branch === meta.def) return null;

  for (const repo of meta.repos) {
    const out = await run(
      "gh",
      ["pr", "list", "-R", repo, "--head", branch, "--state", "open", "--json", "number,isDraft"],
      cwd,
    );
    const hit = out ? JSON.parse(out)[0] : undefined;
    if (hit) return { number: hit.number, draft: hit.isDraft, repo };
  }
  return null;
}

const tui: TuiPlugin = async (api) => {
  const [pr, setPr] = createSignal<Pr | null>(null);
  const theme = () => api.theme.current;
  let last = "";
  let at = 0;
  let busy = false;

  // Resolving costs ~750ms (gh spawn), so it never happens during render. The
  // bookmark lookup is ~20ms, cheap enough to poll as a change detector.
  const refresh = async () => {
    if (busy) return;
    busy = true;
    try {
      const cwd = api.state.path.worktree || api.state.path.directory;
      if (!cwd) return;
      const branch = await currentBranch(cwd);
      if (branch === last && Date.now() - at < STALE_MS) return;
      last = branch;
      at = Date.now();
      setPr(branch ? await resolvePr(cwd, branch) : null);
    } finally {
      busy = false;
    }
  };

  const timer = setInterval(() => void refresh(), POLL_MS);
  api.lifecycle.onDispose(() => clearInterval(timer));
  void refresh();

  const chip = () => {
    const value = pr();
    if (!value) return null;
    return (
      <text fg={theme().textMuted}>
        {value.repo}{" "}
        <span style={{ fg: value.draft ? theme().warning : theme().success }}>#{value.number}</span>
      </text>
    );
  };

  api.slots.register({
    order: 100,
    slots: {
      home_prompt_right: chip,
      session_prompt_right: chip,
    },
  });
};

const plugin: TuiPluginModule & { id: string } = { id: "pr-status", tui };
export default plugin;
