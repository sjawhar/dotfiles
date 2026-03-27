import { readFileSync, writeFileSync } from "fs";

/**
 * Session registry plugin for the oc() shell wrapper.
 *
 * Replaces the bash _oc_enrich polling loop with event-driven updates.
 * Writes session info (id, title) and port to the registry JSON file
 * so that `oc ps` and `tmux-snapshot` can read it.
 *
 * Env vars set by oc() in .bashrc:
 *   OC_REGISTRY  — directory containing per-instance JSON files
 *   OC_SHELL_PID — PID of the shell that launched opencode (= tmux pane_pid)
 */

export default async (ctx) => {
  const shellPid = process.env.OC_SHELL_PID;
  const registryDir = process.env.OC_REGISTRY;
  if (!shellPid || !registryDir) return {};

  const file = `${registryDir}/${shellPid}.json`;
  let activeSessionID: string | null = null;

  const update = (patch: Record<string, unknown>) => {
    try {
      const data = JSON.parse(readFileSync(file, "utf-8"));
      Object.assign(data, patch);
      writeFileSync(file, JSON.stringify(data) + "\n");
    } catch {}
  };

  // Fill in the port (oc() writes it as null before opencode starts)
  try {
    const port = parseInt(new URL(ctx.serverUrl).port);
    if (port) update({ port });
  } catch {}

  const fetchSession = async (sessionID: string) => {
    try {
      const resp = await fetch(`${ctx.serverUrl}/session/${sessionID}`);
      if (resp.ok) {
        const session = await resp.json();
        return { id: session.id, title: session.title || "" };
      }
    } catch {}
    return null;
  };

  return {
    event: async ({ event }) => {
      // Session became busy — user is interacting with this session
      if (
        event.type === "session.status" &&
        event.properties.status?.type === "busy"
      ) {
        const sessionID = event.properties.sessionID;
        if (sessionID && sessionID !== activeSessionID) {
          activeSessionID = sessionID;
          const session = await fetchSession(sessionID);
          if (session) update({ session });
        }
      }

      // Session metadata changed (e.g., title generated after first message)
      if (event.type === "session.updated") {
        const info = event.properties.info;
        if (info && info.id === activeSessionID) {
          update({ session: { id: info.id, title: info.title || "" } });
        }
      }

      // Idle after being busy — good time to refresh title
      if (event.type === "session.idle") {
        const sessionID = event.properties.sessionID;
        if (sessionID && sessionID === activeSessionID) {
          const session = await fetchSession(sessionID);
          if (session) update({ session });
        }
      }
    },
  };
};
