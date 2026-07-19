export default async () => ({
  "shell.env": async (input, output) => {
    if (!input.sessionID) return;
    output.env["OPENCODE_SESSION_ID"] = input.sessionID;
  },
});
