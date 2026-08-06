// fanin-proxy.ts — opencode fan-in proxy for OpenChamber.
//
// Impersonates a single opencode server on :5199. Discovers every live
// opencode process on this machine via envoy's session registry, then:
//   - GET /global/event      → merged SSE from ALL backends (live streaming)
//   - GET /session/status    → fan-out to owning backends, merged busy map
//   - /session/:id/*         → routed to the process that owns the session
//   - everything else        → fallback headless serve
//
// Backends join/leave dynamically as sessions start/stop in tmux.

const PROXY_PORT = 5199
const FALLBACK_PORT = 5096
const ENVOY_URL = Bun.env["ENVOY_URL"] ?? "http://127.0.0.1:9020"
const MACHINE_ID = Bun.env["ENVOY_MACHINE_ID"] ?? "sami-agents"
const DISCOVERY_INTERVAL_MS = 10_000
const TAP_RETRY_MS = 3_000
const HEARTBEAT_MS = 15_000

type EnvoySession = {
  readonly session_id: string
  readonly machine_id: string
  readonly dir: string
  readonly port: number
}

type Registry = {
  readonly owners: ReadonlyMap<string, number>
  readonly dirPorts: ReadonlyMap<string, readonly number[]>
  readonly ports: readonly number[]
}

const encoder = new TextEncoder()
let registry: Registry = { owners: new Map(), dirPorts: new Map(), ports: [FALLBACK_PORT] }
const taps = new Map<number, AbortController>()
const clients = new Set<ReadableStreamDefaultController<Uint8Array>>()
let connectedFrame = `data: {"type":"server.connected","data":{}}\n\n`

function log(line: string): void {
  console.log(`${new Date().toISOString()} ${line}`)
}

function backendUrl(port: number, pathAndQuery: string): string {
  return `http://127.0.0.1:${port}${pathAndQuery}`
}

async function fetchRegistry(): Promise<Registry> {
  const res = await fetch(`${ENVOY_URL}/v1/sessions`)
  const sessions = (await res.json()) as readonly EnvoySession[]
  const local = sessions.filter((s) => s.machine_id === MACHINE_ID)
  const owners = new Map<string, number>()
  const dirPorts = new Map<string, number[]>()
  for (const s of local) {
    owners.set(s.session_id, s.port)
    const list = dirPorts.get(s.dir) ?? []
    if (!list.includes(s.port)) list.push(s.port)
    dirPorts.set(s.dir, list)
  }
  const ports = [...new Set([FALLBACK_PORT, ...local.map((s) => s.port)])]
  return { owners, dirPorts, ports }
}

function broadcast(text: string): void {
  const bytes = encoder.encode(text)
  for (const controller of clients) {
    try {
      controller.enqueue(bytes)
    } catch {
      clients.delete(controller) // no-excuse-ok: catch — closed client, drop it
    }
  }
}

async function tapBackend(port: number, signal: AbortSignal): Promise<void> {
  let buffer = ""
  const res = await fetch(backendUrl(port, "/global/event"), { signal })
  if (!res.body) throw new Error(`no body from :${port}/global/event`)
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  for (;;) {
    const { done, value } = await reader.read()
    if (done) return
    buffer += decoder.decode(value, { stream: true })
    const frames = buffer.split("\n\n")
    buffer = frames.pop() ?? ""
    for (const frame of frames) {
      if (frame.startsWith(":") || frame.length === 0) continue // backend heartbeat
      if (frame.includes('"type":"server.connected"')) {
        connectedFrame = `${frame}\n\n` // keep a verbatim copy to greet new clients
        continue
      }
      broadcast(`${frame}\n\n`)
    }
  }
}

function startTap(port: number): void {
  const controller = new AbortController()
  taps.set(port, controller)
  const run = async (): Promise<void> => {
    while (taps.get(port) === controller) {
      try {
        log(`tap :${port} connecting`)
        await tapBackend(port, controller.signal)
      } catch (e) {
        if (controller.signal.aborted) return
        log(`tap :${port} error: ${e instanceof Error ? e.message : String(e)}`)
      }
      await Bun.sleep(TAP_RETRY_MS)
    }
  }
  void run()
}

async function refreshBackends(): Promise<void> {
  try {
    registry = await fetchRegistry()
  } catch (e) {
    log(`envoy discovery failed: ${e instanceof Error ? e.message : String(e)}`)
    return
  }
  const wanted = new Set(registry.ports)
  for (const port of wanted) {
    if (!taps.has(port)) startTap(port)
  }
  for (const [port, controller] of taps) {
    if (!wanted.has(port)) {
      controller.abort()
      taps.delete(port)
      log(`tap :${port} dropped (no longer registered)`)
    }
  }
}

type StatusEntry = { readonly type: string }

async function collectStatus(pathAndQuery: string, directory: string | null): Promise<Record<string, StatusEntry>> {
  const dirOwned = directory === null ? null : (registry.dirPorts.get(directory) ?? [])
  const ports = [...new Set([FALLBACK_PORT, ...(dirOwned ?? registry.ports)])]
  const results = await Promise.allSettled(
    ports.map(async (port) => {
      const res = await fetch(backendUrl(port, pathAndQuery))
      if (!res.ok) throw new Error(`:${port} -> ${res.status}`)
      return (await res.json()) as Record<string, StatusEntry>
    }),
  )
  const merged: Record<string, StatusEntry> = {}
  for (const result of results) {
    if (result.status !== "fulfilled") continue
    for (const [id, entry] of Object.entries(result.value)) {
      const existing = merged[id]
      if (existing === undefined || existing.type === "idle") merged[id] = entry
    }
  }
  return merged
}


async function forward(port: number, req: Request, pathAndQuery: string): Promise<Response> {
  const headers = new Headers(req.headers)
  headers.delete("host")
  const body = req.method === "GET" || req.method === "HEAD" ? undefined : await req.arrayBuffer()
  const res = await fetch(backendUrl(port, pathAndQuery), {
    method: req.method,
    headers,
    body,
    redirect: "manual",
  })
  return new Response(res.body, { status: res.status, headers: res.headers })
}

function globalEventStream(): Response {
  let heartbeat: ReturnType<typeof setInterval> | undefined
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(encoder.encode(connectedFrame))
      clients.add(controller)
      heartbeat = setInterval(() => {
        try {
          controller.enqueue(encoder.encode(": heartbeat\n\n"))
        } catch {
          clients.delete(controller) // no-excuse-ok: catch — closed client
          clearInterval(heartbeat)
        }
      }, HEARTBEAT_MS)
    },
    cancel() {
      clearInterval(heartbeat)
    },
  })
  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      "X-Accel-Buffering": "no",
    },
  })
}

const SESSION_PATH = /^\/session\/(ses_[A-Za-z0-9]+)/

Bun.serve({
  port: PROXY_PORT,
  hostname: "127.0.0.1",
  idleTimeout: 0,
  async fetch(req) {
    const url = new URL(req.url)
    const pathAndQuery = `${url.pathname}${url.search}`
    if (req.headers.get("upgrade") !== null) {
      return new Response("websocket upgrade not supported by fan-in proxy", { status: 501 })
    }
    if (url.pathname === "/global/event") return globalEventStream()
    if (url.pathname === "/session/status") {
      return Response.json(await collectStatus(pathAndQuery, url.searchParams.get("directory")))
    }
    const sessionMatch = SESSION_PATH.exec(url.pathname)
    const owner = sessionMatch?.[1] !== undefined ? registry.owners.get(sessionMatch[1]) : undefined
    const port = owner ?? FALLBACK_PORT
    try {
      return await forward(port, req, pathAndQuery)
    } catch (e) {
      if (port !== FALLBACK_PORT) {
        log(`owner :${port} unreachable (${e instanceof Error ? e.message : String(e)}), falling back`)
        return forward(FALLBACK_PORT, req, pathAndQuery)
      }
      throw e
    }
  },
})

await refreshBackends()
setInterval(() => void refreshBackends(), DISCOVERY_INTERVAL_MS)
log(`fan-in proxy on :${PROXY_PORT} — fallback :${FALLBACK_PORT}, envoy ${ENVOY_URL}, machine ${MACHINE_ID}`)
