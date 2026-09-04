// jj-snapshot — snapshot the working copy after every mutating tool call so the
// operation log records the state after each agent action. Same design as
// omp/hooks/post/jj-snapshot.ts: `jj util snapshot` is self-debouncing (an
// unchanged working copy records no operation), it runs fire-and-forget behind an
// in-flight guard, and the root lookup itself never snapshots — a snapshot takes
// the repository-wide Git import/export lock, so nothing here may await one.
export default async (ctx) => {
  // A plugin instance is bound to one project directory, so the jj root is
  // resolved once. `undefined` = not yet resolved; `null` = not a jj repo.
  let root: string | null | undefined;
  let inflight = false;

  async function jjRoot(): Promise<string | null> {
    if (root !== undefined) return root;
    const res = await ctx.$`jj root --ignore-working-copy`.cwd(ctx.directory).quiet().nothrow();
    root = res.exitCode === 0 ? res.text().trim() : null;
    return root;
  }

  return {
    "tool.execute.after": async (input) => {
      if (!/^(edit|write|bash|multiedit|apply_patch)$/.test(input.tool)) return;
      const cwd = await jjRoot();
      if (!cwd || inflight) return;
      inflight = true;
      // Fire-and-forget: a missed snapshot is caught by the next tool call.
      void ctx.$`jj util snapshot`
        .cwd(cwd)
        .quiet()
        .nothrow()
        .catch(() => {})
        .finally(() => {
          inflight = false;
        });
    },
  };
};
