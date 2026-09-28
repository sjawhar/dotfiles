// Transcript viewport: the session transcript in an app-owned window on the
// terminal's alternate screen, with the composer (editor, status line,
// widgets) pinned below it. Toggle with `/viewport` or Ctrl+Alt+F;
// `OMP_VIEWPORT=1` opens it when an interactive session starts, and so does
// `OMP_FULLSCREEN=1`, which boxes launched under the mode's first name still
// carry in their container environment across `agentbox restart`.
//
//   PageUp / PageDown        scroll a page (while the editor has focus)
//   Ctrl+Home / Ctrl+End     jump to the top / back to the live tail
//   mouse wheel              scroll three rows
//
// At the live tail the mode renders only the rows that fit. Scrolled back, the
// view is a number of rows above the tail, rendered bottom-up only as deep as
// it needs plus PREFETCH_ROWS more (omp's `renderTail`), so a scroll step costs
// time in proportion to how far back it goes, not to the length of the
// session; only Ctrl+Home renders everything. Frames page through that copy.
// A scroll step re-renders it once it is over a second old, and if output
// arrived at the tail meanwhile it finds the rows it was showing in the new
// copy and keeps them where they were.
//
// Built entirely on public extension API, so upstream omp needs no change:
// `ctx.ui.custom(..., { overlay: true, overlayOptions: { fullscreen: true } })`
// borrows the alternate screen and suspends history retirement while it is the
// topmost overlay, and its factory receives the live TUI. Leaving the mode
// hands the transcript back to the normal screen, where the rows that settled
// meanwhile retire into native scrollback as usual.
//
// Three things here are structural, not documented API: the transcript is
// found among the TUI's children by duck-typing (`renderTail` +
// `peekReplayBatch`), the editor is the focusable descendant with
// `getText`/`handleInput`, and `tui.showOverlay` and `tui.hasOverlay` are
// wrapped while the mode is open (below). If an omp upgrade reshapes the TUI,
// the mode shows "transcript not found" and closes instead of drawing garbage;
// the upstream ask then is a documented ctx.ui accessor for the transcript and
// editor, not the whole mode.
//
// Dialogs opened over the mode (the alt+p model picker, ask, confirm) stay on
// the alternate screen, drawn over the viewport. The engine picks the screen
// from the topmost overlay alone (`fullscreen` decides nothing else: layout
// comes from anchor, width and height, and the alternate frame composites the
// whole overlay stack), so each dialog opened while the mode is up is shown
// with `fullscreen` set and mouse reporting off unless it asked for it;
// without that the engine drops to the normal screen and repaints the whole
// page behind the dialog, and again when it closes.
//
// The editor's app keys (Ctrl+R history search, Ctrl+O, Alt+L and the rest of
// omp's global editor actions) stand down while `tui.hasOverlay()` reports a
// dialog holding the keyboard. This mode is an overlay that hands the keyboard
// back to the editor, so `hasOverlay` leaves it out while it is open; a dialog
// opened over it still counts. The same check gates omp's inline mouse
// click-to-focus (`tui.mouse`, off by default), which would then take wheel
// reports before this mode sees them, so `tui.mouse` stays off.
//
// The alternate frame keeps the terminal cursor hidden and expects a modal to
// draw its own, so the editor uses its software cursor while the mode is open
// and gets the terminal cursor back when it closes.
//
// Known limit: mouse reporting is on while the mode is open, so native text
// selection needs the terminal's bypass modifier (Shift in most terminals, and
// in tmux with `mouse on`).
import type { ExtensionAPI, ExtensionContext } from "@oh-my-pi/pi-coding-agent";
import {
	type Component,
	matchesKey,
	type OverlayOptions,
	parseSgrMouse,
	type TUI,
	truncateToWidth,
} from "@oh-my-pi/pi-tui";

const WHEEL_ROWS = 3;
// Rows rendered above the scrolled-back view, so the next pages come from the same copy.
const PREFETCH_ROWS = 2000;
// A scroll step re-renders the copy once it is this old, so new output shows up.
const COPY_MAX_AGE_MS = 1000;
// Consecutive rows matched to find the view's place in a re-rendered copy.
const ANCHOR_ROWS = 4;
const TOGGLE_KEY = "ctrl+alt+f";

/** The transcript's last rows, rendered bottom-up for the scrolled-back view. */
interface TailCopy {
	width: number;
	rows: readonly string[];
	/** The render reached the transcript's first row. */
	complete: boolean;
	/** When it was rendered (Date.now()). */
	at: number;
}

interface TranscriptView extends Component {
	renderTail(width: number, maxRows: number): readonly string[];
}

interface EditorView extends Component {
	focused: boolean;
	handleInput(data: string): void;
	setUseTerminalCursor?(useTerminalCursor: boolean): void;
}

function isTranscript(component: Component): component is TranscriptView {
	const candidate = component as Partial<Record<"renderTail" | "peekReplayBatch", unknown>>;
	return typeof candidate.renderTail === "function" && typeof candidate.peekReplayBatch === "function";
}

function findEditor(component: Component): EditorView | undefined {
	const candidate = component as Partial<EditorView & { getText: unknown; children: Component[] }>;
	if (typeof candidate.getText === "function" && typeof candidate.handleInput === "function" && "focused" in candidate) {
		return candidate as EditorView;
	}
	for (const child of candidate.children ?? []) {
		const editor = findEditor(child);
		if (editor) return editor;
	}
	return undefined;
}

/** The open viewport: `close` ends it; `closed` settles once it has fully torn down. */
let active: { close(): void; closed: Promise<void> } | undefined;

function open(ctx: ExtensionContext): void {
	if (active || ctx.mode !== "tui" || !ctx.hasUI) return;
	const unsubscribers: Array<() => void> = [];
	// The factory runs asynchronously; a close requested before it does is replayed.
	let finish: (() => void) | undefined;
	let closeRequested = false;
	let focusEditor: (() => void) | undefined;
	const closed = ctx.ui.custom<void>(
		(tui: TUI, theme, _keybindings, done) => {
			finish = () => done();
			if (closeRequested) queueMicrotask(finish);
			// Scrolled back: rows between the view's last row and the transcript's last
			// row. `undefined` follows the live tail.
			let back: number | undefined;
			let width = tui.terminal.columns;
			let height = 1;
			// The transcript's last rows as rendered for the scrolled-back view.
			let copy: TailCopy | undefined;
			const renderCopy = (transcript: TranscriptView, renderWidth: number, depth: number): TailCopy => {
				const cap = depth + PREFETCH_ROWS;
				const rows = transcript.renderTail(renderWidth, cap);
				return { width: renderWidth, rows, complete: rows.length < cap, at: Date.now() };
			};
			// Rows that arrived at the tail between two copies: how much further from
			// the end the view's top rows now sit. 0 when they cannot be found.
			const growth = (old: TailCopy, oldBack: number, fresh: TailCopy): number => {
				const fromEnd = Math.min(old.rows.length, oldBack + height);
				const start = old.rows.length - fromEnd;
				const anchor = old.rows.slice(start, start + ANCHOR_ROWS);
				if (anchor.every(row => row === "")) return 0;
				for (let at = fresh.rows.length - fromEnd; at >= 0; at--) {
					if (anchor.every((row, i) => fresh.rows[at + i] === row)) return fresh.rows.length - fromEnd - at;
				}
				return 0;
			};
			// The editor whose cursor mode this mode switched, restored on close.
			let cursorEditor: EditorView | undefined;

			const locate = () => {
				const index = tui.children.findIndex(isTranscript);
				if (index < 0) return undefined;
				const transcript = tui.children[index] as TranscriptView;
				const editor = tui.children
					.slice(index + 1)
					.map(findEditor)
					.find(editor => editor !== undefined);
				return { index, transcript, editor };
			};

			const scrollBy = (delta: number) => {
				const found = locate();
				if (!found) return;
				let next = (back ?? 0) - delta;
				if (next <= 0) {
					back = undefined;
					copy = undefined;
					tui.requestRender();
					return;
				}
				let current = copy;
				if (
					current === undefined ||
					current.width !== width ||
					Date.now() - current.at > COPY_MAX_AGE_MS ||
					(!current.complete && next + height > current.rows.length)
				) {
					const renewed = renderCopy(found.transcript, width, next + height);
					if (current !== undefined && back !== undefined && current.width === width) {
						next += growth(current, back, renewed);
					}
					current =
						!renewed.complete && next + height > renewed.rows.length
							? renderCopy(found.transcript, width, next + height)
							: renewed;
				}
				copy = current;
				back = Math.min(next, Math.max(0, current.rows.length - height));
				tui.requestRender();
			};

			// Scrolling runs ahead of focus routing, so it works whichever component
			// holds focus inside the mode; it yields to any overlay stacked above it
			// and to anything other than the editor that has taken focus (pickers
			// and selectors page with the same keys).
			const view: Component & { ownsOverlayFocusTarget(component: Component): boolean } = {
				render(renderWidth: number): readonly string[] {
					width = renderWidth;
					const rows = tui.terminal.rows;
					const found = locate();
					if (!found) {
						queueMicrotask(() => done());
						return [theme.fg("warning", "viewport: transcript not found in this omp build; closing")];
					}
					// The TUI re-applies the terminal-cursor mode on every focus change,
					// so this is re-asserted per frame; the call is a no-op when unchanged.
					found.editor?.setUseTerminalCursor?.(false);
					cursorEditor = found.editor;
					const chrome = tui.children.slice(found.index + 1).flatMap(child => child.render(renderWidth));
					height = Math.max(1, rows - chrome.length - 1);
					let window: readonly string[];
					let atTop = false;
					if (back === undefined) {
						window = found.transcript.renderTail(renderWidth, height);
					} else {
						if (copy === undefined || copy.width !== renderWidth) {
							copy = renderCopy(found.transcript, renderWidth, back + height);
						}
						back = Math.min(back, Math.max(0, copy.rows.length - height));
						const end = copy.rows.length - back;
						window = copy.rows.slice(Math.max(0, end - height), end);
						atTop = copy.complete && end <= height;
					}
					const padding = Array.from({ length: Math.max(0, height - window.length) }, () => "");
					const status =
						back === undefined
							? theme.fg("dim", "── viewport · PgUp/PgDn scroll · Ctrl+Home top · Ctrl+Alt+F exit")
							: theme.fg(
									"accent",
									`── ↑ ${atTop ? "top of the transcript" : `${back} rows above the live tail`} · Ctrl+End or PgDn to the live tail`,
								);
					return [...padding, ...window, truncateToWidth(status, renderWidth), ...chrome];
				},
				handleInput(data: string): void {
					// Focus lands here when the mode opens or a dialog above it closes;
					// hand it straight back to the editor so typing behaves natively.
					const editor = locate()?.editor;
					if (!editor) return;
					tui.setFocus(editor);
					editor.handleInput(data);
				},
				invalidate(): void {},
				ownsOverlayFocusTarget: () => true,
			};

			// Registered on the TUI itself rather than through ctx.ui.onTerminalInput:
			// omp drops every extension input listener at startup and on a session
			// switch, which would leave the mode unable to scroll.
			unsubscribers.push(
				tui.addInputListener(data => {
					if (tui.overlayStack.at(-1)?.component !== view) return undefined;
					if (matchesKey(data, TOGGLE_KEY)) {
						done();
						return { consume: true };
					}
					const mouse = data.startsWith("\x1b[<") ? parseSgrMouse(data) : null;
					if (mouse) {
						if (mouse.wheel !== null) scrollBy(mouse.wheel * WHEEL_ROWS);
						return { consume: true };
					}
					const editor = locate()?.editor;
					if (!editor?.focused) return undefined;
					const page = Math.max(1, height - 2);
					if (matchesKey(data, "pageUp")) scrollBy(-page);
					else if (matchesKey(data, "pageDown")) scrollBy(page);
					else if (matchesKey(data, "ctrl+home")) scrollBy(Number.MIN_SAFE_INTEGER);
					else if (matchesKey(data, "ctrl+end")) scrollBy(Number.MAX_SAFE_INTEGER);
					else return undefined;
					return { consume: true };
				}),
			);
			unsubscribers.push(() => cursorEditor?.setUseTerminalCursor?.(tui.getShowHardwareCursor()));

			// Every dialog opened while the mode is up is shown fullscreen (see the
			// header); the originals are put back if the mode closes under one.
			const lifted = new Map<OverlayOptions, OverlayOptions | undefined>();
			const originalShowOverlay = tui.showOverlay;
			const showOverlay: TUI["showOverlay"] = (component, options) => {
				if (options?.fullscreen === true) return originalShowOverlay.call(tui, component, options);
				const fullscreenOptions: OverlayOptions = {
					...options,
					fullscreen: true,
					mouseTracking: options?.mouseTracking ?? false,
				};
				lifted.set(fullscreenOptions, options);
				return originalShowOverlay.call(tui, component, fullscreenOptions);
			};
			tui.showOverlay = showOverlay;
			unsubscribers.push(() => {
				if (tui.showOverlay === showOverlay) tui.showOverlay = originalShowOverlay;
				for (const entry of tui.overlayStack) {
					if (entry.options && lifted.has(entry.options)) entry.options = lifted.get(entry.options);
				}
				tui.requestRender();
			});
			// App keys aimed at the editor defer only to dialogs, not to this mode
			// (see the header). Visibility mirrors the TUI's own check: not hidden,
			// and the overlay's `visible` callback, if any, says so.
			const originalHasOverlay = tui.hasOverlay;
			const hasOverlay: TUI["hasOverlay"] = () =>
				tui.overlayStack.some(
					entry =>
						entry.component !== view &&
						!entry.hidden &&
						(entry.options?.visible?.(tui.terminal.columns, tui.terminal.rows) ?? true),
				);
			tui.hasOverlay = hasOverlay;
			unsubscribers.push(() => {
				if (tui.hasOverlay === hasOverlay) tui.hasOverlay = originalHasOverlay;
			});
			focusEditor = () => {
				const editor = locate()?.editor;
				if (editor) tui.setFocus(editor);
			};
			return view;
		},
		{
			overlay: true,
			overlayOptions: { fullscreen: true, anchor: "top-left", width: "100%", maxHeight: "100%", margin: 0 },
			// showOverlay focuses the overlay itself; give the editor focus back so
			// typing, autocomplete and the cursor behave exactly as outside the mode.
			onHandle: () => focusEditor?.(),
		},
	);
	active = {
		close: () => {
			closeRequested = true;
			finish?.();
		},
		closed: closed.then(
			() => undefined,
			() => undefined,
		),
	};
	void active.closed.finally(() => {
		for (const unsubscribe of unsubscribers) unsubscribe();
		active = undefined;
	});
}

function toggle(ctx: ExtensionContext): void {
	if (active) active.close();
	else open(ctx);
}

export default function viewport(pi: ExtensionAPI): void {
	pi.registerCommand("viewport", {
		description: "Toggle the transcript viewport (alternate screen, composer pinned)",
		handler: async (_args, ctx) => toggle(ctx),
	});
	pi.registerShortcut(TOGGLE_KEY, {
		description: "Toggle the transcript viewport",
		handler: ctx => toggle(ctx),
	});
	pi.on("session_start", async (_event, ctx) => {
		if (process.env.OMP_VIEWPORT === "1" || process.env.OMP_FULLSCREEN === "1") open(ctx);
	});
}
