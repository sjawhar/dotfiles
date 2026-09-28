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
// view is anchored to a transcript block and a row within it
// (viewport-window.ts), so a frame or a scroll step renders only the blocks on
// screen and the ones it passes over, however long the session is. New output
// at the tail does not move a scrolled-back view, and neither do rows below it
// shrinking: the view then shows the last rows and stays scrolled back until
// PgDn or Ctrl+End. A transcript rebuilt from new blocks without the view's
// block (a shake, for example) returns the view to the live tail. Ctrl+Home
// jumps to the first block without rendering the rest. A transcript that fits
// on screen has nothing to scroll back to, whether it fit when the key was
// pressed or came to fit later: PgUp and Ctrl+Home keep the live tail, and a
// scrolled-back view returns to it.
//
// Built entirely on public extension API, so upstream omp needs no change:
// `ctx.ui.custom(..., { overlay: true, overlayOptions: { fullscreen: true } })`
// borrows the alternate screen and suspends history retirement while it is the
// topmost overlay, and its factory receives the live TUI. Leaving the mode
// hands the transcript back to the normal screen, where the rows that settled
// meanwhile retire into native scrollback as usual.
//
// Four things here are structural, not documented API: the transcript is
// found among the TUI's children by duck-typing (`renderTail` +
// `peekReplayBatch` + `children`), the editor is the focusable descendant with
// `getText`/`handleInput`, `tui.showOverlay` and `tui.hasOverlay` are wrapped
// while the mode is open (below), and the scrolled-back view iterates
// `transcript.children` itself, calling each block's `setTranscriptAllocation`
// and `render` and composing the rows as `TranscriptContainer.renderTail` does
// (a copy in viewport-window.ts). The transcript lookup fails loudly: if an omp
// upgrade reshapes the TUI, the mode shows "transcript not found" and closes
// instead of drawing garbage. The block walk drifts silently: a renamed
// `setTranscriptAllocation` shows the blocks the live frame squeezed as compact
// summaries when scrolled back, and a change to `renderTail`'s composition makes
// the scrolled-back view differ from the live one, so either needs the same
// change here. The upstream ask is a documented ctx.ui accessor for the
// transcript and editor, and a transcript method that renders rows from a block
// position so the composition copy can go; not the whole mode.
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
// Known limit: mouse reporting is on while the mode is open, so a plain drag
// does not select. In tmux, Alt+drag selects the on-screen text inside the pane
// (`.tmux.conf`); Shift+drag hands selection to the outer terminal, which
// selects across tmux panes.
import type { ExtensionAPI, ExtensionContext } from "@oh-my-pi/pi-coding-agent";
import {
	type Component,
	matchesKey,
	type OverlayOptions,
	parseSgrMouse,
	type TUI,
	truncateToWidth,
} from "@oh-my-pi/pi-tui";
import {
	type Blocks,
	isTop,
	moveDown,
	moveUp,
	type Position,
	positionFromTail,
	resolve,
	scrolledBack,
	settle,
	top,
	trimBlankEdges,
	windowAt,
} from "./viewport-window";

const WHEEL_ROWS = 3;
const TOGGLE_KEY = "ctrl+alt+f";

interface TranscriptView extends Component {
	children: Component[];
	renderTail(width: number, maxRows: number): readonly string[];
}

interface AllocationTarget {
	setTranscriptAllocation?(rows: number, frame: { tick: number; now: number }): void;
}

interface EditorView extends Component {
	focused: boolean;
	handleInput(data: string): void;
	setUseTerminalCursor?(useTerminalCursor: boolean): void;
}

function isTranscript(component: Component): component is TranscriptView {
	const candidate = component as Partial<Record<"renderTail" | "peekReplayBatch" | "children", unknown>>;
	return (
		typeof candidate.renderTail === "function" &&
		typeof candidate.peekReplayBatch === "function" &&
		Array.isArray(candidate.children)
	);
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
			// Scrolled back: the view's top row. `undefined` follows the live tail.
			let pos: Position<Component> | undefined;
			let width = tui.terminal.columns;
			let height = 1;
			// The transcript's blocks for one frame or one scroll step. The windowing
			// asks for a block's rows several times per operation, so they are
			// memoized for that operation; components cache their own rows across
			// frames.
			const blocksOf = (transcript: TranscriptView, renderWidth: number): Blocks<Component> => {
				const rendered = new Map<Component, readonly string[]>();
				const frame = { tick: 0, now: Date.now() };
				return {
					get length() {
						return transcript.children.length;
					},
					at: index => transcript.children[index]!,
					rows: block => {
						let rows = rendered.get(block);
						if (rows === undefined) {
							// As TranscriptContainer.renderTail does: a full allocation, so a
							// block the live frame squeezed renders whole, not compact.
							(block as Component & AllocationTarget).setTranscriptAllocation?.(Number.MAX_SAFE_INTEGER, frame);
							rows = trimBlankEdges(block.render(renderWidth));
							rendered.set(block, rows);
						}
						return rows;
					},
					// Container.clear() swaps in a new array; removeChild splices in place.
					get source() {
						return transcript.children;
					},
				};
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
				const blocks = blocksOf(found.transcript, width);
				const current = pos === undefined ? undefined : resolve(blocks, pos);
				if (delta < 0) {
					const moved = current === undefined ? positionFromTail(blocks, -delta, height) : moveUp(blocks, current, -delta);
					pos = scrolledBack(blocks, moved, height);
				} else {
					pos = current === undefined ? undefined : moveDown(blocks, current, delta, height);
				}
				tui.requestRender();
			};
			const jumpTop = () => {
				const found = locate();
				if (!found) return;
				const blocks = blocksOf(found.transcript, width);
				pos = scrolledBack(blocks, top(blocks), height);
				tui.requestRender();
			};
			const jumpTail = () => {
				pos = undefined;
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
					let scrolled: { rows: readonly string[]; where: string } | undefined;
					if (pos !== undefined) {
						const blocks = blocksOf(found.transcript, renderWidth);
						pos = settle(blocks, pos, height);
						if (pos !== undefined) {
							scrolled = {
								rows: windowAt(blocks, pos, height).rows,
								where: isTop(blocks, pos)
									? "top of the transcript"
									: `block ${(pos.index + 1).toLocaleString("en-US")} of ${blocks.length.toLocaleString("en-US")}`,
							};
						}
					}
					const window = scrolled?.rows ?? found.transcript.renderTail(renderWidth, height);
					const status = scrolled
						? theme.fg("accent", `── ↑ ${scrolled.where} · Ctrl+End or PgDn to the live tail`)
						: theme.fg("dim", "── viewport · PgUp/PgDn scroll · Ctrl+Home top · Ctrl+Alt+F exit");
					const padding = Array.from({ length: Math.max(0, height - window.length) }, () => "");
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
					else if (matchesKey(data, "ctrl+home")) jumpTop();
					else if (matchesKey(data, "ctrl+end")) jumpTail();
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
