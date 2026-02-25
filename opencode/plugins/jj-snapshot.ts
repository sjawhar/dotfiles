export default async (ctx) => ({
  "tool.execute.after": async (input) => {
    if (!/^(edit|write|bash|multiedit|apply_patch)$/.test(input.tool)) return;
    await ctx.$`jj root && jj st`.quiet().nothrow();
  },
});
