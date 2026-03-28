# Response: Presenting Audit Findings in the Browser

## Approach: Visual Companion (not raw HTML)

I'd use the **visual companion system** from the brainstorming skill — the same server/screen-directory pattern referenced in the overseer's `reference.md` under "Visual Companion."

### Why this, not raw HTML

The overseer skill explicitly says: "offer to push status/plans to the browser via the visual companion." The reference.md spells out the uses: status dashboards, approval flows, information-dense displays, and plans/status for review. Audit findings are exactly this — information-dense, structured data that benefits from visual treatment and is awkward to review on a phone via terminal.

The visual companion provides:
- **A frame template** with themed CSS, responsive layout, and interactive infrastructure — no need to write full HTML documents
- **Clickable options** with event capture — the user can approve/reject/flag findings from their phone
- **Tailscale accessibility** — the reference notes hostname `sami-agents-mx.tailb86685.ts.net`, reachable from the user's phone
- **Content fragments** — I write just the content; the server wraps it in the frame template automatically

### What I'd do (step by step)

1. **Dispatch a sub-agent** to start the visual companion server:
   ```bash
   scripts/start-server.sh --project-dir /path/to/project --host 0.0.0.0 --url-host sami-agents-mx.tailb86685.ts.net
   ```
   Save the `screen_dir` from the response.

2. **Gather the audit findings** via another sub-agent (from whatever source — GitHub, session messages, a report file).

3. **Write an HTML content fragment** (not a full document) to `$SCREEN_DIR/audit-findings.html` using the visual companion's built-in CSS classes:

   ```html
   <h2>Audit Findings</h2>
   <p class="subtitle">12 findings across 3 workstreams — tap to review</p>

   <div class="section">
     <div class="label">Critical (2)</div>
     <div class="options">
       <div class="option" data-choice="finding-1" onclick="toggleSelect(this)">
         <div class="letter">!</div>
         <div class="content">
           <h3>Auth token leaked in PR #247</h3>
           <p>Credentials committed to docker-compose.yml — needs immediate rotation</p>
         </div>
       </div>
       <!-- ... more findings ... -->
     </div>
   </div>

   <div class="section">
     <div class="label">Warnings (6)</div>
     <div class="cards">
       <div class="card" data-choice="finding-3" onclick="toggleSelect(this)">
         <div class="card-body">
           <h3>Missing error handling in payment flow</h3>
           <p>Worker completed but skipped retry logic</p>
         </div>
       </div>
       <!-- ... more findings ... -->
     </div>
   </div>
   ```

4. **Tell the user** the URL and what's on screen:
   > "I've pushed the 12 audit findings to your browser — grouped by severity. You can tap any finding to flag it for follow-up. Take a look and let me know which ones need action."

5. **On the next turn**, read `$SCREEN_DIR/.events` to see which findings the user tapped/selected, merge with any terminal response, and act accordingly.

### What I would NOT do

- **Generate raw HTML from scratch** with inline styles, `<html><head><style>...` boilerplate — the visual companion handles all of that via the frame template
- **Start a random HTTP server** (`python -m http.server`) — the visual companion server already exists and handles file watching, event capture, and the frame template
- **Dump findings as terminal text** — the user specifically asked for browser/phone review, and the overseer skill says to use the visual companion for information-dense displays
- **Do the server work in the main thread** — all operational work (starting server, gathering findings, writing HTML) is dispatched to sub-agents per overseer discipline
