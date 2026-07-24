# secretsd OpenCode Plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an OpenCode plugin that creates an unguessable per-session token, registers it with secretsd, exposes a value-free `secrets_request` tool, and leaves every OpenCode session usable when secretsd is absent.

**Architecture:** `opencode/plugins/secretsd.ts` follows the existing `session-env.ts` `shell.env` hook and `session-registry.ts` event-hook closure pattern. The closure owns `Map<sessionID, SessionState>`; `session.created.properties.info.id` creates a 256-bit token file and launches a detached, short-deadline registration, while `session.deleted.properties.info.id` unregisters and removes it. `shell.env` uses its confirmed `input.sessionID` to recover a missed state, injects only `SECRETSD_SESSION_TOKEN_FILE`, and awaits `ensureRegistered(sessionID)` with a short control deadline before the shell can run the human-tier shim. The tool uses the in-memory token, never emits a bearer credential or secret value, and retries a request once after an `UNKNOWN_TOKEN` re-registration. `dispose` aborts live long-poll requests, unregisters every tracked session, and removes every token file.

**Tech Stack:** TypeScript; OpenCode plugin SDK (`tool`); Bun 1.3.13 native test runner and `Bun.connect({ unix })`; Node-compatible `fs`, `path`, and `crypto` APIs; Bun fake Unix-domain socket server.

## Global Constraints

- Use jj, never git; make **one commit for the whole deliverable**, not one commit per task.
- Modify only `opencode/plugins/secretsd.ts`, `opencode/plugins/secretsd.test.ts`, and `opencode/opencode.json`; do not change shims, SOPS files, installers, scripts, existing plugins, or add a Claude Code path.
- Every hook and tool must catch operational failures and return normally; no exception may escape an OpenCode hook.
- Generate exactly 256 random bits with `crypto.randomBytes(32).toString("hex")` (64 lowercase hex characters); never use `Math.random`.
- Accept a session ID for a token filename only when it matches `/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/`; reject other IDs before constructing a path. The daemon still receives that original, validated session ID in `REGISTER` and `UNREGISTER`.
- Store tokens at `${XDG_RUNTIME_DIR}/secretsd/<sessionID>.token`; ensure the directory is mode `0700` and each token file is mode `0600`. Do not substitute `/tmp` when `XDG_RUNTIME_DIR` is absent.
- `SECRETSD_SESSION_TOKEN_FILE` contains only that token-file path. Never put a token value in an environment variable, log line, tool result, test assertion output, or transcript-facing metadata.
- Never put a secret value in any log line or tool result. `secrets_request` returns only `granted`, `denied`, or `unavailable` plus short guidance; `REQUEST` never returns a `pending` success response.
- Use a fake Unix-socket broker in every test. Tests must never contact `$XDG_RUNTIME_DIR/secretsd.sock` or a real secretsd process.
- The daemon protocol is newline-delimited: `HELLO\tversion=1` → exactly `OK\tversion=1`; `REGISTER\ttoken=<64hex>\tsession=<id>\tpid=<n>` and `UNREGISTER\tsession=<id>` → exactly `OK`; `REQUEST\tkey=<KEY>\ttoken=<64hex>` blocks through the daemon's approval flow and returns exactly `OK\tstatus=granted` on success. It otherwise returns `ERR\t<CODE>\t<msg>`, where the v1 codes are `BAD_REQUEST`, `UNKNOWN_OP`, `VERSION_MISMATCH`, `UNKNOWN_TOKEN`, `NO_SCOPE`, `AGENT_TTY`, `NOT_HUMAN_KEY`, `DENIED`, `TIMEOUT`, `YUBIKEY_UNREACHABLE`, `NOT_ANNOUNCED`, `TOO_MANY_PENDING`, and `INTERNAL`.
- Use a 2-second timeout for `HELLO`, `REGISTER`, and `UNREGISTER`. `REQUEST` uses an abort-aware 100-second timeout—longer than the daemon's default 90-second `request_ttl`—and closes the socket on either timeout or plugin disposal. `DENIED` and `TIMEOUT` map to `denied`; every other request error maps to `unavailable` except an initial `UNKNOWN_TOKEN`, which triggers one persisted-token registration and one retry. `VERSION_MISMATCH` is a loud, unavailable identity/protocol error; no path falls back to a tokenless request.
- Per-session tokens provide workflow scoping and audit for same-UID processes, not hard isolation. Do not claim otherwise.

**Rollout order is safety-critical:** this plugin may deploy before the human migration ceremony; before the daemon is available its detached registration fails harmlessly and agent-tier secrets continue normally. The rewritten `secrets` shim must deploy **only after** the ceremony creates `secrets.human.d/`: it derives the human-key set from that directory and cannot safely route human-tier access before it exists.

**Hook evidence:** `opencode/plugins/session-registry.ts` confirms `session.updated.properties.info`, `session.idle.properties.sessionID`, and `session.status.properties.sessionID`; `opencode/plugins/session-env.ts` confirms `shell.env` receives `input.sessionID`. Current OpenCode plugin event definitions additionally specify `session.created` and `session.deleted`, each with `properties.info.id`, so this plan uses those real names and paths rather than inventing an event shape.

## File Structure

| Path | Action | Responsibility |
| --- | --- | --- |
| `opencode/plugins/secretsd.ts` | Create | Token-file lifecycle, Bun Unix-socket protocol client, session event and shell hooks, and `secrets_request`. |
| `opencode/plugins/secretsd.test.ts` | Create | Bun unit/integration tests against an in-process fake Unix socket, including transcript-safety assertions. |
| `opencode/opencode.json` | Modify | Register `file://{env:HOME}/.dotfiles/opencode/plugins/secretsd.ts` immediately after `session-env.ts`. |

**Test setup decision:** No TypeScript test setup exists for `opencode/plugins` (the only repository Bun tests are under `envoy/__tests__`). Introduce no package/config churn: Bun discovers `opencode/plugins/secretsd.test.ts` directly. Run exactly `cd /home/sami/.dotfiles/opencode && bun test plugins/secretsd.test.ts`; expected output is `0 fail`. The repository pins Bun 1.3.13 in `mise.toml`.

**Socket decision:** Use `Bun.connect({ unix: socketPath, socket: { open, data, error, close } })`. OpenCode 1.18.4-sami executes plugins in Bun (with Node compatibility), and `Bun.connect` is the runtime-native Unix-socket API. `net.createConnection({ path })` would work only through Bun's Node compatibility layer; spawning `socat` adds an external binary, process overhead, and error surface. Tests use `Bun.listen({ unix: socketPath, socket: ... })` for the fake broker.

---

### Task 1: Issue private token files and inject only their paths

**Files:**
- Create: `opencode/plugins/secretsd.ts`
- Create: `opencode/plugins/secretsd.test.ts`

**Interfaces:**
- Produces: `issueTokenFile(runtimeDir, sessionID): SessionState`, `removeTokenFile(state)`, and `createSecretsdPlugin(options)` for the later broker and tool tasks.
- Consumes: documented `session.created.properties.info.id` and the existing `session-env.ts` `shell.env` hook's `input.sessionID`.

- [ ] **Step 1: Write the failing token-file and environment tests**

Create `opencode/plugins/secretsd.test.ts`:

```ts
import { afterEach, describe, expect, test } from "bun:test";
import { existsSync, mkdtempSync, readFileSync, rmSync, statSync } from "fs";
import { join } from "path";
import { createSecretsdPlugin, issueTokenFile } from "./secretsd";

const roots: string[] = [];
const root = () => {
  const value = mkdtempSync("/tmp/secretsd-plugin-");
  roots.push(value);
  return value;
};

afterEach(() => {
  for (const value of roots.splice(0)) rmSync(value, { force: true, recursive: true });
});

describe("secretsd token issuance", () => {
  test("writes a 256-bit token to a 0600 file in a 0700 directory", async () => {
    const runtimeDir = root();
    const plugin = createSecretsdPlugin({ runtimeDir, socketPath: join(runtimeDir, "missing.sock"), pid: 42 });

    await plugin.hooks.event({ event: { type: "session.created", properties: { info: { id: "session-a" } } } });

    const file = join(runtimeDir, "secretsd", "session-a.token");
    expect(existsSync(file)).toBe(true);
    const tokenMatchesFormat = /^[0-9a-f]{64}$/.test(readFileSync(file, "utf8"));
    expect(tokenMatchesFormat).toBe(true);
    expect(statSync(join(runtimeDir, "secretsd")).mode & 0o777).toBe(0o700);
    expect(statSync(file).mode & 0o777).toBe(0o600);
  });

  test("adds only the token-file path to the session shell environment", async () => {
    const runtimeDir = root();
    const plugin = createSecretsdPlugin({ runtimeDir, socketPath: join(runtimeDir, "missing.sock"), pid: 42 });
    await plugin.hooks.event({ event: { type: "session.created", properties: { info: { id: "session-b" } } } });
    const output = { env: {} as Record<string, string> };

    await plugin.hooks["shell.env"]({ sessionID: "session-b" }, output);

    expect(output.env.SECRETSD_SESSION_TOKEN_FILE).toBe(join(runtimeDir, "secretsd", "session-b.token"));
    const token = readFileSync(output.env.SECRETSD_SESSION_TOKEN_FILE, "utf8");
    const environmentContainsToken = Object.values(output.env).some((value) => value === token);
    expect(environmentContainsToken).toBe(false);
  });

  test("rejects an unsafe session ID before deriving a token filename", () => {
    const runtimeDir = root();
    expect(() => issueTokenFile(runtimeDir, "../other-session")).toThrow("invalid session ID");
    expect(existsSync(join(runtimeDir, "secretsd", "other-session.token"))).toBe(false);
  });
});
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `cd /home/sami/.dotfiles/opencode && bun test plugins/secretsd.test.ts`

Expected: `FAIL` because `./secretsd` does not exist.

- [ ] **Step 3: Write the complete initial plugin implementation**

Create `opencode/plugins/secretsd.ts`:

```ts
import { randomBytes } from "crypto";
import { chmodSync, mkdirSync, rmSync, writeFileSync } from "fs";
import { join } from "path";

type SessionState = { token: string; tokenFile: string };
type PluginOptions = { runtimeDir?: string; socketPath?: string; pid?: number };
type EventInput = { event: { type: string; properties: { info?: { id?: string } } } };
type ShellInput = { sessionID?: string };
type ShellOutput = { env: Record<string, string> };

const sessionIDPattern = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/;

function validateSessionID(sessionID: string): void {
  if (!sessionIDPattern.test(sessionID)) throw new Error("invalid session ID");
}

function tokenDirectory(runtimeDir: string): string {
  return join(runtimeDir, "secretsd");
}

function tokenFile(runtimeDir: string, sessionID: string): string {
  validateSessionID(sessionID);
  return join(tokenDirectory(runtimeDir), `${sessionID}.token`);
}

export function issueTokenFile(runtimeDir: string, sessionID: string): SessionState {
  const directory = tokenDirectory(runtimeDir);
  mkdirSync(directory, { recursive: true, mode: 0o700 });
  chmodSync(directory, 0o700);
  const token = randomBytes(32).toString("hex");
  const tokenPath = tokenFile(runtimeDir, sessionID);
  writeFileSync(tokenPath, token, { encoding: "utf8", mode: 0o600 });
  chmodSync(tokenPath, 0o600);
  return { token, tokenFile: tokenPath };
}

export function removeTokenFile(state: SessionState): void {
  rmSync(state.tokenFile, { force: true });
}

export function createSecretsdPlugin(options: PluginOptions = {}) {
  const runtimeDir = options.runtimeDir ?? process.env.XDG_RUNTIME_DIR;
  const states = new Map<string, SessionState>();

  const ensureState = (sessionID: string): SessionState | undefined => {
    if (!runtimeDir) return undefined;
    const existing = states.get(sessionID);
    if (existing) return existing;
    const state = issueTokenFile(runtimeDir, sessionID);
    states.set(sessionID, state);
    return state;
  };

  const hooks = {
    event: async ({ event }: EventInput): Promise<void> => {
      try {
        if (event.type !== "session.created") return;
        const sessionID = event.properties.info?.id;
        if (!sessionID) return;
        ensureState(sessionID);
      } catch {}
    },
    "shell.env": async (input: ShellInput, output: ShellOutput): Promise<void> => {
      try {
        const state = input.sessionID ? ensureState(input.sessionID) : undefined;
        if (state) output.env.SECRETSD_SESSION_TOKEN_FILE = state.tokenFile;
      } catch {}
    },
  };

  return { hooks, states };
}

export default async () => createSecretsdPlugin().hooks;
```

- [ ] **Step 4: Run the focused test to verify token issuance and path-only injection**

Run: `cd /home/sami/.dotfiles/opencode && bun test plugins/secretsd.test.ts`

Expected: `3 pass`, `0 fail`. The tests reduce token observations to booleans before asserting, so a failure cannot print a bearer token into test output.

### Task 2: Register and revoke session identities through the broker

**Files:**
- Modify: `opencode/plugins/secretsd.ts`
- Modify: `opencode/plugins/secretsd.test.ts`

**Interfaces:**
- Consumes: `SessionState` from Task 1 and the daemon's `HELLO`, `REGISTER`, and `UNREGISTER` wire messages.
- Produces: `BrokerClient.register(state, sessionID, pid)`, `BrokerClient.unregister(sessionID)`, `ensureRegistered(sessionID)`, and lifecycle handling for `session.created`, `session.deleted`, `shell.env`, and `dispose`.

`session.created` creates the file and starts registration detached with the 2-second control deadline; it never waits on a stale socket and therefore never delays or breaks session startup. `shell.env` is the pre-shell guarantee: it injects the file path, then awaits `ensureRegistered(sessionID)` with the same bounded deadline before a shell can issue `GET`. Registration is intentionally idempotent and runs on every shell environment hook, so a daemon restart is repaired before human-tier shell access.

- [ ] **Step 1: Add failing fake-broker lifecycle tests**

Append this complete helper and test to `opencode/plugins/secretsd.test.ts`:

```ts
function fakeBroker(socketPath: string) {
  const received: string[] = [];
  let buffered = "";
  const server = Bun.listen({
    unix: socketPath,
    socket: {
      data(socket, data) {
        buffered += new TextDecoder().decode(data);
        for (;;) {
          const newline = buffered.indexOf("\n");
          if (newline < 0) return;
          const line = buffered.slice(0, newline);
          buffered = buffered.slice(newline + 1);
          received.push(line);
          socket.write(line === "HELLO\tversion=1" ? "OK\tversion=1\n" : "OK\n");
        }
      },
    },
  });
  return { received, stop: () => server.stop(true) };
}

function redactFrames(frames: readonly string[]): string[] {
  return frames.map((frame) => frame.replace(/token=[0-9a-f]{64}/g, "token=<TOKEN>"));
}

async function eventually(predicate: () => boolean): Promise<boolean> {
  const deadline = Date.now() + 1_000;
  while (Date.now() < deadline) {
    if (predicate()) return true;
    await Bun.sleep(10);
  }
  return predicate();
}

test("registers at creation and unregisters plus removes the file at deletion", async () => {
  const runtimeDir = root();
  const socketPath = join(runtimeDir, "broker.sock");
  const broker = fakeBroker(socketPath);
  const plugin = createSecretsdPlugin({ runtimeDir, socketPath, pid: 4242 });

  await plugin.hooks.event({ event: { type: "session.created", properties: { info: { id: "session-c" } } } });
  expect(await eventually(() => broker.received.length === 2)).toBe(true);
  await plugin.hooks.event({ event: { type: "session.deleted", properties: { info: { id: "session-c" } } } });

  expect(redactFrames(broker.received)).toEqual([
    "HELLO\tversion=1",
    "REGISTER\ttoken=<TOKEN>\tsession=session-c\tpid=4242",
    "HELLO\tversion=1",
    "UNREGISTER\tsession=session-c",
  ]);
  expect(existsSync(join(runtimeDir, "secretsd", "session-c.token"))).toBe(false);
  broker.stop();
});

test("shell.env re-registers a persisted session before shell access", async () => {
  const runtimeDir = root();
  const socketPath = join(runtimeDir, "broker.sock");
  const plugin = createSecretsdPlugin({ runtimeDir, socketPath, pid: 77 });
  await plugin.hooks.event({ event: { type: "session.created", properties: { info: { id: "session-restart" } } } });
  const broker = fakeBroker(socketPath);
  const output = { env: {} as Record<string, string> };

  await plugin.hooks["shell.env"]({ sessionID: "session-restart" }, output);

  expect(redactFrames(broker.received)).toEqual([
    "HELLO\tversion=1",
    "REGISTER\ttoken=<TOKEN>\tsession=session-restart\tpid=77",
  ]);
  expect(output.env.SECRETSD_SESSION_TOKEN_FILE).toBe(join(runtimeDir, "secretsd", "session-restart.token"));
  broker.stop();
});

test("session.created never waits on a stale broker socket", async () => {
  const runtimeDir = root();
  const socketPath = join(runtimeDir, "broker.sock");
  const server = Bun.listen({
    unix: socketPath,
    socket: { data() {} },
  });
  const plugin = createSecretsdPlugin({ runtimeDir, socketPath, pid: 78 });
  const eventReturned = await Promise.race([
    plugin.hooks.event({ event: { type: "session.created", properties: { info: { id: "session-stale" } } } }).then(() => true),
    Bun.sleep(100).then(() => false),
  ]);

  expect(eventReturned).toBe(true);
  server.stop(true);
  await plugin.hooks.dispose();
});

test("dispose unregisters every live session and removes its token file", async () => {
  const runtimeDir = root();
  const socketPath = join(runtimeDir, "broker.sock");
  const broker = fakeBroker(socketPath);
  const plugin = createSecretsdPlugin({ runtimeDir, socketPath, pid: 88 });
  await plugin.hooks.event({ event: { type: "session.created", properties: { info: { id: "session-dispose" } } } });
  expect(await eventually(() => broker.received.length === 2)).toBe(true);

  await plugin.hooks.dispose();

  expect(redactFrames(broker.received)).toEqual([
    "HELLO\tversion=1",
    "REGISTER\ttoken=<TOKEN>\tsession=session-dispose\tpid=88",
    "HELLO\tversion=1",
    "UNREGISTER\tsession=session-dispose",
  ]);
  expect(existsSync(join(runtimeDir, "secretsd", "session-dispose.token"))).toBe(false);
  broker.stop();
});
```

- [ ] **Step 2: Run the lifecycle test to verify it fails**

Run: `cd /home/sami/.dotfiles/opencode && bun test plugins/secretsd.test.ts -t "registers at creation"`

Expected: `FAIL` because Task 1 has no `BrokerClient` and does not send protocol messages.

- [ ] **Step 3: Replace the plugin with the complete lifecycle implementation**

Replace `opencode/plugins/secretsd.ts` with:

```ts
import { randomBytes } from "crypto";
import { chmodSync, mkdirSync, rmSync, writeFileSync } from "fs";
import { join } from "path";

type SessionState = { token: string; tokenFile: string };
type PluginOptions = { runtimeDir?: string; socketPath?: string; pid?: number };
type EventInput = { event: { type: string; properties: { info?: { id?: string } } } };
type ShellInput = { sessionID?: string };
type ShellOutput = { env: Record<string, string> };

const decoder = new TextDecoder();
const CONTROL_TIMEOUT_MS = 2_000;
const sessionIDPattern = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/;

class ProtocolVersionMismatch extends Error {}

function validateSessionID(sessionID: string): void {
  if (!sessionIDPattern.test(sessionID)) throw new Error("invalid session ID");
}

const tokenDirectory = (runtimeDir: string) => join(runtimeDir, "secretsd");
const tokenFile = (runtimeDir: string, sessionID: string) => {
  validateSessionID(sessionID);
  return join(tokenDirectory(runtimeDir), `${sessionID}.token`);
};

export function issueTokenFile(runtimeDir: string, sessionID: string): SessionState {
  const directory = tokenDirectory(runtimeDir);
  mkdirSync(directory, { recursive: true, mode: 0o700 });
  chmodSync(directory, 0o700);
  const token = randomBytes(32).toString("hex");
  const tokenPath = tokenFile(runtimeDir, sessionID);
  writeFileSync(tokenPath, token, { encoding: "utf8", mode: 0o600 });
  chmodSync(tokenPath, 0o600);
  return { token, tokenFile: tokenPath };
}

export function removeTokenFile(state: SessionState): void {
  rmSync(state.tokenFile, { force: true });
}

class BrokerClient {
  constructor(private readonly socketPath: string) {}

  private async line(command: string, timeoutMs: number, signal?: AbortSignal): Promise<string> {
    return new Promise((resolve, reject) => {
      let response = "";
      let settled = false;
      let closeSocket: (() => void) | undefined;
      let timer: ReturnType<typeof setTimeout> | undefined;
      const finish = (result: string | Error) => {
        if (settled) return;
        settled = true;
        if (timer) clearTimeout(timer);
        signal?.removeEventListener("abort", abort);
        if (result instanceof Error) reject(result); else resolve(result);
      };
      const abort = () => {
        closeSocket?.();
        finish(new Error("broker request aborted"));
      };
      if (signal?.aborted) return abort();
      signal?.addEventListener("abort", abort, { once: true });
      timer = setTimeout(() => {
        closeSocket?.();
        finish(new Error("broker timeout"));
      }, timeoutMs);
      Bun.connect({
        unix: this.socketPath,
        socket: {
          open(socket) {
            if (settled) return socket.end();
            closeSocket = () => socket.end();
            socket.write(`${command}\n`);
          },
          data(socket, data) {
            response += decoder.decode(data);
            const newline = response.indexOf("\n");
            if (newline >= 0) { socket.end(); finish(response.slice(0, newline)); }
          },
          error() { finish(new Error("broker connection failed")); },
          close() { if (!settled) finish(new Error("broker closed without a response")); },
        },
      }).catch(() => finish(new Error("broker connection failed")));
    });
  }

  private async command(message: string, timeoutMs = CONTROL_TIMEOUT_MS, signal?: AbortSignal): Promise<string> {
    const hello = await this.line("HELLO\tversion=1", CONTROL_TIMEOUT_MS, signal);
    if (hello === "ERR\tVERSION_MISMATCH" || hello.startsWith("ERR\tVERSION_MISMATCH\t")) {
      throw new ProtocolVersionMismatch();
    }
    if (hello !== "OK\tversion=1") throw new Error("broker rejected HELLO");
    const response = await this.line(message, timeoutMs, signal);
    if (response === "ERR\tVERSION_MISMATCH" || response.startsWith("ERR\tVERSION_MISMATCH\t")) {
      throw new ProtocolVersionMismatch();
    }
    return response;
  }

  async register(state: SessionState, sessionID: string, pid: number): Promise<void> {
    const response = await this.command(`REGISTER\ttoken=${state.token}\tsession=${sessionID}\tpid=${pid}`);
    if (response !== "OK") throw new Error("broker registration rejected");
  }

  async unregister(sessionID: string): Promise<void> {
    const response = await this.command(`UNREGISTER\tsession=${sessionID}`);
    if (response !== "OK") throw new Error("broker unregistration rejected");
  }
}

export function createSecretsdPlugin(options: PluginOptions = {}) {
  const runtimeDir = options.runtimeDir ?? process.env.XDG_RUNTIME_DIR;
  const broker = new BrokerClient(options.socketPath ?? (runtimeDir ? join(runtimeDir, "secretsd.sock") : ""));
  const pid = options.pid ?? process.pid;
  const states = new Map<string, SessionState>();
  const ensureState = (sessionID: string): SessionState | undefined => {
    if (!runtimeDir) return undefined;
    const existing = states.get(sessionID);
    if (existing) return existing;
    const state = issueTokenFile(runtimeDir, sessionID);
    states.set(sessionID, state);
    return state;
  };
  const ensureRegistered = async (sessionID: string): Promise<SessionState | undefined> => {
    const state = ensureState(sessionID);
    if (!state) return undefined;
    await broker.register(state, sessionID, pid);
    return state;
  };
  const removeState = (sessionID: string, state: SessionState) => {
    try { removeTokenFile(state); } catch {}
    states.delete(sessionID);
  };
  const hooks = {
    event: async ({ event }: EventInput): Promise<void> => {
      try {
        if (event.type === "session.created") {
          const sessionID = event.properties.info?.id;
          if (!sessionID || !ensureState(sessionID)) return;
          void ensureRegistered(sessionID).catch(() => {});
        }
        if (event.type === "session.deleted") {
          const sessionID = event.properties.info?.id;
          if (!sessionID) return;
          const state = states.get(sessionID);
          if (!state) return;
          try { await broker.unregister(sessionID); } catch {}
          removeState(sessionID, state);
        }
      } catch {}
    },
    "shell.env": async (input: ShellInput, output: ShellOutput): Promise<void> => {
      try {
        if (!input.sessionID) return;
        const state = ensureState(input.sessionID);
        if (!state) return;
        output.env.SECRETSD_SESSION_TOKEN_FILE = state.tokenFile;
        await ensureRegistered(input.sessionID);
      } catch {}
    },
    dispose: async (): Promise<void> => {
      await Promise.allSettled([...states.entries()].map(async ([sessionID, state]) => {
        try { await broker.unregister(sessionID); } catch {} finally { removeState(sessionID, state); }
      }));
    },
  };
  return { hooks, states };
}

export default async () => createSecretsdPlugin().hooks;
```

- [ ] **Step 4: Run all current plugin tests**

Run: `cd /home/sami/.dotfiles/opencode && bun test plugins/secretsd.test.ts`

Expected: `7 pass`, `0 fail`. The fake server sees the exact `OK\tversion=1` HELLO response, normalized token frames only, shell-time re-registration, nonblocking stale-socket lifecycle registration, session deletion cleanup, and shutdown cleanup; no real socket is contacted.

### Task 3: Add the transcript-safe `secrets_request` tool

**Files:**
- Modify: `opencode/plugins/secretsd.ts`
- Modify: `opencode/plugins/secretsd.test.ts`

**Interfaces:**
- Consumes: `BrokerClient.register`, persisted `SessionState`, and OpenCode's `tool({ args, execute })` API.
- Produces: an MCP tool named `secrets_request` whose output is a value-free `granted`, `denied`, or `unavailable` guidance string.

- [ ] **Step 1: Add failing request/re-registration and no-value tests**

Append this fake broker and test to `opencode/plugins/secretsd.test.ts`:

```ts
test("re-registers once after UNKNOWN_TOKEN and returns value-free granted guidance", async () => {
  const runtimeDir = root();
  const socketPath = join(runtimeDir, "broker.sock");
  let requestCount = 0;
  let registrations = 0;
  const received: string[] = [];
  let buffered = "";
  const server = Bun.listen({
    unix: socketPath,
    socket: {
      data(socket, data) {
        buffered += new TextDecoder().decode(data);
        for (;;) {
          const newline = buffered.indexOf("\n");
          if (newline < 0) return;
          const line = buffered.slice(0, newline);
          buffered = buffered.slice(newline + 1);
          received.push(line);
          if (line === "HELLO\tversion=1") socket.write("OK\tversion=1\n");
          else if (line.startsWith("REGISTER\t")) { registrations += 1; socket.write("OK\n"); }
          else if (line.startsWith("REQUEST\t")) {
            requestCount += 1;
            socket.write(requestCount === 1 ? "ERR\tUNKNOWN_TOKEN\tbroker restarted\n" : "OK\tstatus=granted\n");
          } else socket.write("OK\n");
        }
      },
    },
  });
  const plugin = createSecretsdPlugin({ runtimeDir, socketPath, pid: 99 });
  await plugin.hooks.event({ event: { type: "session.created", properties: { info: { id: "session-d" } } } });
  expect(await eventually(() => registrations === 1)).toBe(true);

  const result = await plugin.hooks.tool.secrets_request.execute(
    { key: "PULUMI_CONFIG_PASSPHRASE" },
    { sessionID: "session-d" },
  );

  expect(registrations).toBe(2);
  expect(requestCount).toBe(2);
  const isGranted = result.startsWith("granted:");
  const resultContainsToken = /[0-9a-f]{64}/.test(result);
  expect(isGranted).toBe(true);
  expect(resultContainsToken).toBe(false);
  expect(redactFrames(received)).toEqual([
    "HELLO\tversion=1",
    "REGISTER\ttoken=<TOKEN>\tsession=session-d\tpid=99",
    "HELLO\tversion=1",
    "REQUEST\tkey=PULUMI_CONFIG_PASSPHRASE\ttoken=<TOKEN>",
    "HELLO\tversion=1",
    "REGISTER\ttoken=<TOKEN>\tsession=session-d\tpid=99",
    "HELLO\tversion=1",
    "REQUEST\tkey=PULUMI_CONFIG_PASSPHRASE\ttoken=<TOKEN>",
  ]);
  await plugin.hooks.dispose();
  server.stop(true);
});

async function requestGuidanceFor(responseFrame: string): Promise<string> {
  const runtimeDir = root();
  const socketPath = join(runtimeDir, "broker.sock");
  let registrations = 0;
  let buffered = "";
  const server = Bun.listen({
    unix: socketPath,
    socket: {
      data(socket, data) {
        buffered += new TextDecoder().decode(data);
        for (;;) {
          const newline = buffered.indexOf("\n");
          if (newline < 0) return;
          const line = buffered.slice(0, newline);
          buffered = buffered.slice(newline + 1);
          if (line === "HELLO\tversion=1") socket.write("OK\tversion=1\n");
          else if (line.startsWith("REGISTER\t")) { registrations += 1; socket.write("OK\n"); }
          else if (line.startsWith("REQUEST\t")) socket.write(`${responseFrame}\n`);
          else socket.write("OK\n");
        }
      },
    },
  });
  const plugin = createSecretsdPlugin({ runtimeDir, socketPath, pid: 101 });
  await plugin.hooks.event({ event: { type: "session.created", properties: { info: { id: "session-errors" } } } });
  expect(await eventually(() => registrations === 1)).toBe(true);
  const result = await plugin.hooks.tool.secrets_request.execute(
    { key: "PULUMI_CONFIG_PASSPHRASE" },
    { sessionID: "session-errors" },
  );
  await plugin.hooks.dispose();
  server.stop(true);
  return result;
}

test("maps every non-version, non-token REQUEST error from the daemon", async () => {
  const cases = [
    ["ERR\tBAD_REQUEST\tbad request", "unavailable"],
    ["ERR\tUNKNOWN_OP\tunknown operation", "unavailable"],
    ["ERR\tNO_SCOPE\tno scope", "unavailable"],
    ["ERR\tAGENT_TTY\tagent tty", "unavailable"],
    ["ERR\tNOT_HUMAN_KEY\tnot human", "unavailable"],
    ["ERR\tDENIED\tdenied", "denied"],
    ["ERR\tTIMEOUT\ttimed out", "denied"],
    ["ERR\tYUBIKEY_UNREACHABLE\tunreachable", "unavailable"],
    ["ERR\tNOT_ANNOUNCED\tnot announced", "unavailable"],
    ["ERR\tTOO_MANY_PENDING\tqueue full", "unavailable"],
    ["ERR\tINTERNAL\tinternal", "unavailable"],
  ] as const;

  for (const [frame, expectedStatus] of cases) {
    const result = await requestGuidanceFor(frame);
    const hasExpectedStatus = result.startsWith(`${expectedStatus}:`);
    expect(hasExpectedStatus).toBe(true);
  }
});
```

- [ ] **Step 2: Run the tool test to verify it fails**

Run: `cd /home/sami/.dotfiles/opencode && bun test plugins/secretsd.test.ts -t "re-registers once|maps every non-version"`

Expected: `FAIL` because the plugin has no `tool.secrets_request` entry or `REQUEST` handling.

- [ ] **Step 3: Add the complete request and tool implementation**

Replace the `BrokerClient` class in `opencode/plugins/secretsd.ts` with this complete class, and add the `requestSecret` function and `tool` member exactly as shown:

```ts
export const REQUEST_TIMEOUT_MS = 100_000;
type RequestStatus = "granted" | "denied" | "unavailable";
type RequestOutcome = { status: RequestStatus; versionMismatch: boolean };

class BrokerClient {
  constructor(private readonly socketPath: string) {}

  private async line(command: string, timeoutMs: number, signal?: AbortSignal): Promise<string> {
    return new Promise((resolve, reject) => {
      let response = "";
      let settled = false;
      let closeSocket: (() => void) | undefined;
      let timer: ReturnType<typeof setTimeout> | undefined;
      const finish = (result: string | Error) => {
        if (settled) return;
        settled = true;
        if (timer) clearTimeout(timer);
        signal?.removeEventListener("abort", abort);
        if (result instanceof Error) reject(result); else resolve(result);
      };
      const abort = () => {
        closeSocket?.();
        finish(new Error("broker request aborted"));
      };
      if (signal?.aborted) return abort();
      signal?.addEventListener("abort", abort, { once: true });
      timer = setTimeout(() => {
        closeSocket?.();
        finish(new Error("broker timeout"));
      }, timeoutMs);
      Bun.connect({
        unix: this.socketPath,
        socket: {
          open(socket) {
            if (settled) return socket.end();
            closeSocket = () => socket.end();
            socket.write(`${command}\n`);
          },
          data(socket, data) {
            response += decoder.decode(data);
            const newline = response.indexOf("\n");
            if (newline >= 0) { socket.end(); finish(response.slice(0, newline)); }
          },
          error() { finish(new Error("broker connection failed")); },
          close() { if (!settled) finish(new Error("broker closed without a response")); },
        },
      }).catch(() => finish(new Error("broker connection failed")));
    });
  }

  private async command(message: string, timeoutMs = CONTROL_TIMEOUT_MS, signal?: AbortSignal): Promise<string> {
    const hello = await this.line("HELLO\tversion=1", CONTROL_TIMEOUT_MS, signal);
    if (hello === "ERR\tVERSION_MISMATCH" || hello.startsWith("ERR\tVERSION_MISMATCH\t")) {
      throw new ProtocolVersionMismatch();
    }
    if (hello !== "OK\tversion=1") throw new Error("broker rejected HELLO");
    const response = await this.line(message, timeoutMs, signal);
    if (response === "ERR\tVERSION_MISMATCH" || response.startsWith("ERR\tVERSION_MISMATCH\t")) {
      throw new ProtocolVersionMismatch();
    }
    return response;
  }

  async register(state: SessionState, sessionID: string, pid: number): Promise<void> {
    const response = await this.command(`REGISTER\ttoken=${state.token}\tsession=${sessionID}\tpid=${pid}`);
    if (response !== "OK") throw new Error("broker registration rejected");
  }

  async unregister(sessionID: string): Promise<void> {
    const response = await this.command(`UNREGISTER\tsession=${sessionID}`);
    if (response !== "OK") throw new Error("broker unregistration rejected");
  }

  async request(key: string, state: SessionState, signal: AbortSignal): Promise<string> {
    return this.command(`REQUEST\tkey=${key}\ttoken=${state.token}`, REQUEST_TIMEOUT_MS, signal);
  }
}

function guidance(outcome: RequestOutcome): string {
  if (outcome.versionMismatch) {
    return "unavailable: secretsd protocol version mismatch; restart or update secretsd and OpenCode before requesting a secret.";
  }
  if (outcome.status === "granted") return "granted: use the secrets shim for the requested key.";
  if (outcome.status === "denied") return "denied: the request was denied or timed out; make a new request only if appropriate.";
  return "unavailable: secretsd could not complete this human-tier request; check that the broker and YubiKey are available.";
}

function responseCode(response: string): string | undefined {
  if (!response.startsWith("ERR\t")) return undefined;
  return response.split("\t", 3)[1];
}

async function requestSecret(
  broker: BrokerClient,
  state: SessionState,
  sessionID: string,
  pid: number,
  key: string,
  signal: AbortSignal,
): Promise<RequestOutcome> {
  try {
    let response = await broker.request(key, state, signal);
    if (responseCode(response) === "UNKNOWN_TOKEN") {
      await broker.register(state, sessionID, pid);
      response = await broker.request(key, state, signal);
    }
    if (response === "OK\tstatus=granted") return { status: "granted", versionMismatch: false };
    if (responseCode(response) === "DENIED" || responseCode(response) === "TIMEOUT") {
      return { status: "denied", versionMismatch: false };
    }
    return { status: "unavailable", versionMismatch: false };
  } catch (error) {
    return { status: "unavailable", versionMismatch: error instanceof ProtocolVersionMismatch };
  }
}
```

Add this import at the top of the same file:

```ts
import { tool } from "@opencode-ai/plugin";
```

Immediately before the existing `const hooks = {` in `createSecretsdPlugin`, add the plugin-lifetime abort controller:

```ts
const requestAbort = new AbortController();
```

Then add this member beside `event` and `"shell.env"` in the `hooks` object:

```ts

tool: {
  secrets_request: tool({
    description: "Request human approval for a secretsd human-tier key; never returns secret values.",
    args: { key: tool.schema.string().regex(/^[A-Z][A-Z0-9_]*$/) },
    async execute({ key }, context) {
      try {
        const state = states.get(context.sessionID);
        if (!state) return guidance({ status: "unavailable", versionMismatch: false });
        return guidance(await requestSecret(broker, state, context.sessionID, pid, key, requestAbort.signal));
      } catch {
        return guidance({ status: "unavailable", versionMismatch: false });
      }
    },
  }),
},
```

- [ ] **Step 4: Run the complete tool suite**

Run: `cd /home/sami/.dotfiles/opencode && bun test plugins/secretsd.test.ts`

Expected: `9 pass`, `0 fail`. The tests prove `UNKNOWN_TOKEN` causes one registration and one retry, verify normalized frames only, and map every real terminal daemon error to a value-free result.

### Task 4: Abort pending requests on shutdown and register the plugin

**Files:**
- Modify: `opencode/plugins/secretsd.ts`
- Modify: `opencode/plugins/secretsd.test.ts`
- Modify: `opencode/opencode.json`

**Interfaces:**
- Consumes: Task 3's status-only request boundary, its plugin-lifetime `requestAbort` controller, and the existing `plugin` array convention.
- Produces: a registered plugin that remains graceful when the broker is absent, calls out a version mismatch, aborts a live `REQUEST` during shutdown, and still removes every token file.

- [ ] **Step 1: Add absent-broker, version-mismatch, and disposal-abort tests**

Replace the existing import with this exact import, then append the tests:

```ts
import { REQUEST_TIMEOUT_MS, createSecretsdPlugin, issueTokenFile } from "./secretsd";

test("keeps an OpenCode session usable when the broker socket is absent", async () => {
  const runtimeDir = root();
  const plugin = createSecretsdPlugin({ runtimeDir, socketPath: join(runtimeDir, "absent.sock"), pid: 7 });
  await plugin.hooks.event({ event: { type: "session.created", properties: { info: { id: "session-e" } } } });
  const output = { env: {} as Record<string, string> };
  await plugin.hooks["shell.env"]({ sessionID: "session-e" }, output);

  const result = await plugin.hooks.tool.secrets_request.execute(
    { key: "DEEL_API_KEY" },
    { sessionID: "session-e" },
  );

  expect(output.env.SECRETSD_SESSION_TOKEN_FILE).toBe(join(runtimeDir, "secretsd", "session-e.token"));
  const isUnavailable = result.startsWith("unavailable:");
  expect(isUnavailable).toBe(true);
  await plugin.hooks.dispose();
});

test("reports a broker version mismatch loudly without a tokenless fallback", async () => {
  const runtimeDir = root();
  const socketPath = join(runtimeDir, "broker.sock");
  const server = Bun.listen({
    unix: socketPath,
    socket: { data(socket) { socket.write("ERR\tVERSION_MISMATCH\tupgrade required\n"); } },
  });
  const plugin = createSecretsdPlugin({ runtimeDir, socketPath, pid: 8 });
  await plugin.hooks.event({ event: { type: "session.created", properties: { info: { id: "session-f" } } } });

  const result = await plugin.hooks.tool.secrets_request.execute(
    { key: "PULUMI_CONFIG_PASSPHRASE" },
    { sessionID: "session-f" },
  );

  const mentionsVersionMismatch = result.includes("protocol version mismatch");
  const mentionsTokenlessFallback = result.includes("tokenless");
  expect(mentionsVersionMismatch).toBe(true);
  expect(mentionsTokenlessFallback).toBe(false);
  await plugin.hooks.dispose();
  server.stop(true);
});

test("dispose aborts a live REQUEST instead of waiting for its 100-second deadline", async () => {
  const runtimeDir = root();
  const socketPath = join(runtimeDir, "broker.sock");
  let registrations = 0;
  let requests = 0;
  let buffered = "";
  const server = Bun.listen({
    unix: socketPath,
    socket: {
      data(socket, data) {
        buffered += new TextDecoder().decode(data);
        for (;;) {
          const newline = buffered.indexOf("\n");
          if (newline < 0) return;
          const line = buffered.slice(0, newline);
          buffered = buffered.slice(newline + 1);
          if (line === "HELLO\tversion=1") socket.write("OK\tversion=1\n");
          else if (line.startsWith("REGISTER\t")) { registrations += 1; socket.write("OK\n"); }
          else if (line.startsWith("REQUEST\t")) requests += 1;
          else socket.write("OK\n");
        }
      },
    },
  });
  const plugin = createSecretsdPlugin({ runtimeDir, socketPath, pid: 9 });
  await plugin.hooks.event({ event: { type: "session.created", properties: { info: { id: "session-abort" } } } });
  expect(await eventually(() => registrations === 1)).toBe(true);

  const request = plugin.hooks.tool.secrets_request.execute(
    { key: "PULUMI_CONFIG_PASSPHRASE" },
    { sessionID: "session-abort" },
  );
  expect(await eventually(() => requests === 1)).toBe(true);
  await plugin.hooks.dispose();
  const result = await Promise.race([
    request,
    Bun.sleep(250).then(() => "still-waiting"),
  ]);

  const requestWasAborted = result !== "still-waiting" && result.startsWith("unavailable:");
  expect(REQUEST_TIMEOUT_MS).toBe(100_000);
  expect(requestWasAborted).toBe(true);
  expect(existsSync(join(runtimeDir, "secretsd", "session-abort.token"))).toBe(false);
  server.stop(true);
});
```

- [ ] **Step 2: Run the shutdown test to verify it fails before disposal abort is added**

Run: `cd /home/sami/.dotfiles/opencode && bun test plugins/secretsd.test.ts -t "dispose aborts"`

Expected: `FAIL` after 250 ms because Task 3's `dispose` cleanup unregisters and removes files but does not yet abort the live `REQUEST`.

- [ ] **Step 3: Abort requests first during disposal and register the plugin**

Replace the Task 2 `dispose` member with this complete version:

```ts
dispose: async (): Promise<void> => {
  requestAbort.abort();
  await Promise.allSettled([...states.entries()].map(async ([sessionID, state]) => {
    try { await broker.unregister(sessionID); } catch {} finally { removeState(sessionID, state); }
  }));
},
```

In `opencode/opencode.json`, add this exact line after the existing `session-env.ts` entry:

```json
"file://{env:HOME}/.dotfiles/opencode/plugins/secretsd.ts",
```

- [ ] **Step 4: Run the complete test suite and validate configuration**

Run: `cd /home/sami/.dotfiles/opencode && bun test plugins/secretsd.test.ts && bun -e 'JSON.parse(require("fs").readFileSync("opencode.json", "utf8")); console.log("opencode.json valid")'`

Expected: `12 pass`, `0 fail`, followed by `opencode.json valid`. All tests use a temporary fake broker socket or an intentionally absent temporary path; no test can contact real secretsd.

- [ ] **Step 5: Create the single jj commit after verification**

Run:

```bash
cd /home/sami/.dotfiles && \
  jj diff --git -- opencode/plugins/secretsd.ts opencode/plugins/secretsd.test.ts opencode/opencode.json && \
  jj describe -m "feat: add secretsd OpenCode plugin" && \
  jj bookmark list && \
  jj tug && \
  jj git push
```

Expected: jj records and pushes one commit containing only `opencode/plugins/secretsd.ts`, `opencode/plugins/secretsd.test.ts`, and `opencode/opencode.json`. Restart OpenCode after the change: plugins and config are loaded only at startup.

## Self-review

- **Spec coverage:** Task 1 covers cryptographic 256-bit token generation, safe session-ID filenames, `0700`/`0600` storage, and path-only injection. Task 2 covers exact HELLO control traffic, nonblocking lifecycle registration, pre-shell re-registration, session deletion, and `dispose` cleanup. Task 3 covers the 100-second abort-aware `REQUEST`, only real `granted` success, all daemon error mappings, and one `UNKNOWN_TOKEN` re-registration/retry. Task 4 aborts pending requests during disposal, proves degradation remains graceful, registers the plugin, and creates the one commit. Agent-tier behavior remains untouched; no Claude Code, shim, installer, migration, SOPS, or secret-file work is included.
- **Protocol check:** `HELLO\tversion=1` accepts only `OK\tversion=1`. `REGISTER` and `UNREGISTER` accept only `OK`. `REQUEST` accepts only `OK\tstatus=granted`; it blocks server-side rather than reporting `pending` or `denied` success fields. `DENIED` and `TIMEOUT` become `denied`; `BAD_REQUEST`, `UNKNOWN_OP`, `VERSION_MISMATCH`, `UNKNOWN_TOKEN` after its retry, `NO_SCOPE`, `AGENT_TTY`, `NOT_HUMAN_KEY`, `YUBIKEY_UNREACHABLE`, `NOT_ANNOUNCED`, `TOO_MANY_PENDING`, and `INTERNAL` become `unavailable`.
- **Token and transcript safety:** Only `REGISTER` and `REQUEST` carry a token over the Unix socket. The path-only environment contract remains exactly `SECRETSD_SESSION_TOKEN_FILE=${XDG_RUNTIME_DIR}/secretsd/<sessionID>.token`. Tests normalize observed frames to `token=<TOKEN>` before comparison and reduce token-presence checks to booleans, so failure output cannot disclose a test bearer token.
- **Lifecycle and rollout:** The event hook uses `event.properties.info.id` for `session.created` and `session.deleted`; the shell hook uses `input.sessionID`; the tool uses `context.sessionID`. The plugin may deploy before the migration ceremony, but the rewritten shim may not deploy until `secrets.human.d/` exists. Same-UID token scope is workflow/audit scoping, never hard isolation.
- **Placeholder scan:** This document contains no deferred implementation markers or implicit test steps. Every code change, command, expected result, socket mechanism, token path, environment variable name, timeout, and error behavior is explicit.
