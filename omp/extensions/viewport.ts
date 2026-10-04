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
//   mouse drag               select and copy; double-click a word, triple-click a row
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
// Five things here are structural, not documented API: the transcript is
// found among the TUI's children by duck-typing (`renderTail` +
// `peekReplayBatch` + `children`), the editor is the focusable descendant with
// `getText`/`handleInput`, `tui.showOverlay` and `tui.hasOverlay` are wrapped
// while the mode is open (below), the scrolled-back view iterates
// `transcript.children` itself, calling each block's `setTranscriptAllocation`
// and `render` and composing the rows as `TranscriptContainer.renderTail` does
// (a copy in viewport-window.ts), and the selection (viewport-selection.ts)
// cuts rendered rows with pi-tui's native `sliceWithWidth`, relying on how it
// treats escapes: those before a slice's start are replayed ahead of its first
// character, the ones right after its end are copied, and one exactly at the
// start comes out ahead of the replayed ones, so the text just after a
// highlight can show the style from before it (cosmetic, as in upstream PR
// #10793). The transcript lookup fails loudly: if an omp upgrade reshapes the
// TUI, the mode shows "transcript not found" and closes instead of drawing
// garbage. The block walk drifts silently: a renamed `setTranscriptAllocation`
// shows the blocks the live frame squeezed as compact summaries when scrolled
// back, and a change to `renderTail`'s composition makes the scrolled-back view
// differ from the live one, so either needs the same change here. A change to
// the slicing rules shows up as a highlight that drops or repeats characters,
// which the selection tests' stand-in would no longer model. The upstream ask
// is a documented ctx.ui accessor for the transcript and editor, and a
// transcript method that renders rows from a block position so the composition
// copy can go; not the whole mode.
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
// Selection: a plain left drag over the transcript selects it, a double-click
// selects a word (a path or URL stays whole) and a triple-click a row, and
// dragging after a double or triple click extends by words or rows. The
// selection is drawn in reverse video and copied on release as OSC 52 through
// pi-tui's terminal writer (tmux's `set-clipboard on` keeps it as a tmux
// buffer), and the footer shows `copied N lines` for 2 s. A click without a
// drag copies nothing. A drag whose release goes elsewhere, seen as pointer
// motion with no button held (a drag continued with Alt, which tmux takes over
// and copies), keeps its highlight and copies nothing, so tmux's copy stands.
// Only the left button's release ends a drag. Once the pointer has moved up or
// down, holding it on the top transcript line or anywhere below the transcript
// scrolls 3 rows every 50 ms and takes whole rows. If the release is lost
// there (tmux took the drag), the scrolling, and the highlight with it,
// continue until the next mouse report arrives; the clipboard is unaffected.
// The ends are blocks and offsets, like the scrolled-back view, so a selection
// stays on its text through scrolling and new output. It clears on a new plain
// left click; on a key that edits or submits (typed text, a paste, Enter,
// Backspace, Delete, Tab, and Shift+Enter, Shift, Ctrl or Alt with Backspace
// or Delete, Shift+Tab, Ctrl+Enter, Ctrl+Q, Alt+Enter), while Esc, the arrows,
// other Ctrl and Alt keys and the scroll keys keep it; on a width change; when
// its block leaves the transcript; and when a selected block's drawn row
// re-renders differently (a block still streaming). The clipboard keeps what
// was copied. The copy is the rows as drawn, trailing spaces trimmed:
// soft-wrapped rows are not joined. Image rows are neither highlighted nor
// copied: the highlight would strip an image's escapes and leave its
// tmux-wrapped placement unterminated, which swallows the rest of the frame,
// so the row stays as drawn and the copy has an empty line in its place. An
// Alt, Shift, Ctrl, middle, right or extra-button press is ignored, so in tmux
// Alt+drag still selects the on-screen text inside the pane (`.tmux.conf`),
// and Shift+drag hands selection to the outer terminal, which selects across
// tmux panes.
import type { ExtensionAPI, ExtensionContext } from "@oh-my-pi/pi-coding-agent";
import {
	BRACKETED_PASTE_START,
	type Component,
	extractPrintableText,
	type KeyId,
	matchesKey,
	type OverlayOptions,
	parseSgrMouse,
	type SgrMouseEvent,
	sliceWithWidth,
	stripTerminalSequences,
	TERMINAL,
	type TUI,
	truncateToWidth,
	visibleWidth,
	writeThroughActiveTerminal,
} from "@oh-my-pi/pi-tui";
import {
	type ClickState,
	copyText,
	type Frame,
	frameAt,
	nextClick,
	paintRows,
	pinnedColumn,
	resolveSelection,
	type Selection,
	snapshotChanged,
	snapshotRows,
	spanAt,
	type TextOps,
	unitFor,
} from "./viewport-selection";
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
} from "./viewport-window";

const WHEEL_ROWS = 3;
const TOGGLE_KEY = "ctrl+alt+f";
/** Edge scrolling while a drag holds the pointer on the top row or below the transcript. */
const EDGE_SCROLL_MS = 50;
const NOTICE_MS = 2000;
/**
 * SGR mouse button-code bits: the low two name the button (0 left, 3 none
 * held), 64 and 128 extend it (the wheel, buttons 8-11), and 4, 8 and 16 are
 * Shift, Alt and Ctrl.
 */
const BUTTON_BITS = 3 | 64 | 128;
const MODIFIER_BITS = 4 | 8 | 16;

/** pi-tui's measuring and slicing, which count OSC 66 text sizing and APC images as the terminal does, and its image-row test. */
const textOps: TextOps = {
	visibleWidth,
	sliceWithWidth,
	stripTerminalSequences,
	isImageLine: line => TERMINAL.isImageLine(line),
};

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

/**
 * Enter, Backspace, Delete and Tab, plain and as Shift+Enter, Shift, Ctrl or Alt
 * with Backspace or Delete, and Shift+Tab; and omp's other submit and newline
 * keys: Ctrl+Enter and Ctrl+Q (a follow-up submit) and Alt+Enter (a newline).
 */
const CLEARING_KEYS: KeyId[] = [
	"enter",
	"shift+enter",
	"ctrl+enter",
	"alt+enter",
	"ctrl+q",
	"backspace",
	"shift+backspace",
	"ctrl+backspace",
	"alt+backspace",
	"delete",
	"shift+delete",
	"ctrl+delete",
	"alt+delete",
	"tab",
	"shift+tab",
];

/**
 * Keys that edit or submit clear a selection: printable text, a bracketed
 * paste, and CLEARING_KEYS. Every other key keeps it. Key releases and
 * terminal replies match none of these.
 */
function clearsSelection(data: string): boolean {
	return (
		data.startsWith(BRACKETED_PASTE_START) ||
		extractPrintableText(data) !== undefined ||
		CLEARING_KEYS.some(key => matchesKey(data, key))
	);
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

			// Mouse selection (see the header). The rules live in viewport-selection.ts;
			// this is the wiring. A drag in progress keeps its own state in `drag`.
			let selection: Selection<Component> | undefined;
			let drag:
				| { pressRow: number; movedVertically: boolean; pointer: { row: number; col: number }; timer?: Timer }
				| undefined;
			let clicks: ClickState | undefined;
			// The transcript rows the last frame drew, with their positions. Undefined
			// after a frame at the live tail with no selection, which draws renderTail's rows.
			let drawn: Frame<Component> | undefined;
			let notice: { text: string; timer: Timer } | undefined;

			const stopEdge = () => {
				clearInterval(drag?.timer);
				if (drag) drag.timer = undefined;
			};
			const clearSelection = () => {
				stopEdge();
				selection = undefined;
				drag = undefined;
			};
			unsubscribers.push(() => {
				stopEdge();
				clearTimeout(notice?.timer);
			});
			// The focus follows the pointer; at an edge it takes whole rows.
			const follow = () => {
				if (selection === undefined || drag === undefined || drawn === undefined) return;
				const { row, col } = drag.pointer;
				const column = pinnedColumn(row, col, drag.movedVertically, height, width);
				selection.focus = spanAt(textOps, drawn, row, column, selection.unit, width) ?? selection.focus;
			};
			const edgeTick = () => {
				const found = locate();
				if (tui.overlayStack.at(-1)?.component !== view || drag === undefined || !found) {
					stopEdge();
					return;
				}
				const up = drag.pointer.row <= 0;
				const blocks = blocksOf(found.transcript, width);
				if (up) {
					const current = pos === undefined ? positionFromTail(blocks, 0, height) : resolve(blocks, pos);
					if (current === undefined || isTop(blocks, current)) return;
				} else if (pos === undefined) return;
				scrollBy(up ? -WHEEL_ROWS : WHEEL_ROWS);
				// The frame scrollBy requested renders later; hit-test against the rows it will draw.
				drawn = frameAt(blocks, pos, height);
				follow();
			};
			/**
			 * Ends a drag and keeps the selection highlighted, or drops it when empty.
			 * `copy` is false for a drag ended by a report with no button held: its
			 * release went elsewhere, typically to tmux after the drag continued with
			 * Alt, and tmux has just copied its own selection, which a copy here
			 * would overwrite.
			 */
			const endDrag = (copy: boolean) => {
				stopEdge();
				drag = undefined;
				const found = locate();
				if (selection === undefined || !found) return;
				tui.requestRender();
				const blocks = blocksOf(found.transcript, width);
				const range = resolveSelection(blocks, selection);
				if (range === undefined || range.empty) {
					selection = undefined;
					return;
				}
				selection.snapshot = snapshotRows(blocks, range);
				if (!copy) return;
				const { text, lines } = copyText(textOps, blocks, range, width);
				// Selected blank cells have no text: nothing to copy, so the clipboard keeps what it holds.
				if (text.length === 0) return;
				const written = writeThroughActiveTerminal(`\x1b]52;c;${Buffer.from(text).toString("base64")}\x07`);
				clearTimeout(notice?.timer);
				notice = {
					text: written ? `copied ${lines} line${lines === 1 ? "" : "s"}` : "copy failed: no active terminal",
					timer: setTimeout(() => {
						notice = undefined;
						tui.requestRender();
					}, NOTICE_MS),
				};
			};
			const onMouse = (mouse: SgrMouseEvent) => {
				if (!mouse.release && !mouse.motion) {
					// Only a plain left press selects: an Alt or Shift press is ignored, so
					// Alt+drag and Shift+drag stay with tmux and the outer terminal, and so
					// are Ctrl, middle, right and extra-button presses.
					if ((mouse.button & (BUTTON_BITS | MODIFIER_BITS)) !== 0) return;
					if (mouse.row >= height) {
						if (selection !== undefined) {
							clearSelection();
							tui.requestRender();
						}
						return;
					}
					const found = locate();
					if (!found) return;
					stopEdge();
					drawn ??= frameAt(blocksOf(found.transcript, width), pos, height);
					clicks = nextClick(clicks, performance.now(), mouse.row, mouse.col);
					const unit = unitFor(clicks.count);
					const anchor = spanAt(textOps, drawn, mouse.row, mouse.col, unit, width);
					selection = anchor && { anchor, focus: anchor, unit, width };
					drag = anchor && { pressRow: mouse.row, movedVertically: false, pointer: { row: mouse.row, col: mouse.col } };
					tui.requestRender();
					return;
				}
				if (drag === undefined) return;
				const button = mouse.button & BUTTON_BITS;
				// The left button's release ends the drag and copies, whatever modifiers
				// it carries; another button's release leaves it running. A motion
				// report with no button held also ends it, without a copy: it proves the
				// release went elsewhere.
				if (mouse.release) {
					if (button === 0) endDrag(true);
					return;
				}
				if (button === 3) {
					endDrag(false);
					return;
				}
				if (button !== 0) return;
				drag.pointer = { row: mouse.row, col: mouse.col };
				if (mouse.row !== drag.pressRow) drag.movedVertically = true;
				follow();
				if (drag.movedVertically && (mouse.row <= 0 || mouse.row >= height)) {
					drag.timer ??= setInterval(edgeTick, EDGE_SCROLL_MS);
				} else {
					stopEdge();
				}
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
					// A new width rewraps every row, so a selection would point at other text.
					if (selection !== undefined && selection.width !== renderWidth) clearSelection();
					const blocks = blocksOf(found.transcript, renderWidth);
					let where: string | undefined;
					if (pos !== undefined) {
						pos = settle(blocks, pos, height);
						if (pos !== undefined) {
							where = isTop(blocks, pos)
								? "top of the transcript"
								: `block ${(pos.index + 1).toLocaleString("en-US")} of ${blocks.length.toLocaleString("en-US")}`;
						}
					}
					// With a selection the live tail is drawn from the same composition
					// renderTail uses, so every row has a position to highlight against.
					drawn = pos === undefined && selection === undefined ? undefined : frameAt(blocks, pos, height);
					let window = drawn?.rows ?? found.transcript.renderTail(renderWidth, height);
					if (drawn !== undefined && selection !== undefined) {
						const range = resolveSelection(blocks, selection);
						// A selection whose block left the transcript, or whose drawn rows
						// re-rendered since the release (a block still streaming), is cleared.
						if (range === undefined || (selection.snapshot !== undefined && snapshotChanged(drawn, selection.snapshot))) {
							clearSelection();
						} else {
							window = paintRows(textOps, drawn, range, renderWidth);
						}
					}
					const status =
						notice !== undefined
							? theme.fg("accent", `── ${notice.text}`)
							: where !== undefined
								? theme.fg("accent", `── ↑ ${where} · Ctrl+End or PgDn to the live tail`)
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
						if (mouse.wheel === null) onMouse(mouse);
						else scrollBy(mouse.wheel * WHEEL_ROWS);
						return { consume: true };
					}
					// Keys that edit or submit clear the selection and still reach the editor.
					if (selection !== undefined && clearsSelection(data)) {
						clearSelection();
						tui.requestRender();
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
