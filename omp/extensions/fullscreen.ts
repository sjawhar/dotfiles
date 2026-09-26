// Fullscreen transcript viewport: the session transcript in an app-owned
// window on the terminal's alternate screen, with the composer (editor, status
// line, widgets) pinned below it. Toggle with `/fullscreen` or Ctrl+Alt+F;
// `OMP_FULLSCREEN=1` opens it when an interactive session starts.
//
//   PageUp / PageDown        scroll a page (while the editor has focus)
//   Ctrl+Home / Ctrl+End     jump to the top / back to the live tail
//   mouse wheel              scroll three rows
//
// Built entirely on public extension API, so upstream omp needs no change:
// `ctx.ui.custom(..., { overlay: true, overlayOptions: { fullscreen: true } })`
// borrows the alternate screen and suspends history retirement while it is the
// topmost overlay, and its factory receives the live TUI. Leaving the mode
// hands the transcript back to the normal screen, where the rows that settled
// meanwhile retire into native scrollback as usual.
//
// Two things here are structural, not documented API: the transcript is found
// among the TUI's children by duck-typing (`renderTail` + `peekReplayBatch`),
// and the editor is the focusable descendant with `getText`/`handleInput`. If
// an omp upgrade reshapes the TUI, the mode shows "transcript not found" and
// closes instead of drawing garbage; the upstream ask then is a documented
// ctx.ui accessor for the transcript and editor, not the whole mode.
//
// Known limits: while a non-fullscreen dialog (ask, confirm) sits on top, the
// engine leaves the alternate screen until it closes; mouse reporting is on
// while the mode is open, so native text selection needs the terminal's
// bypass modifier (Shift in most terminals, and in tmux with `mouse on`).
import type { ExtensionAPI, ExtensionContext } from "@oh-my-pi/pi-coding-agent";
import { type Component, matchesKey, parseSgrMouse, type TUI, truncateToWidth } from "@oh-my-pi/pi-tui";

const WHEEL_ROWS = 3;
const TOGGLE_KEY = "ctrl+alt+f";

interface TranscriptView extends Component {
	renderTail(width: number, maxRows: number): readonly string[];
}

interface EditorView extends Component {
	focused: boolean;
	handleInput(data: string): void;
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
			// The window's top row while scrolled back; `undefined` follows the live tail.
			let top: number | undefined;
			let width = tui.terminal.columns;
			let height = 1;
			let total = 0;

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
				total = found.transcript.render(width).length;
				const bottom = Math.max(0, total - height);
				const next = Math.max(0, Math.min(bottom, (top ?? bottom) + delta));
				top = next >= bottom ? undefined : next;
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
						return [theme.fg("warning", "fullscreen: transcript not found in this omp build; closing")];
					}
					const chrome = tui.children.slice(found.index + 1).flatMap(child => child.render(renderWidth));
					height = Math.max(1, rows - chrome.length - 1);
					let window: readonly string[];
					if (top === undefined) {
						window = found.transcript.renderTail(renderWidth, height);
					} else {
						const all = found.transcript.render(renderWidth);
						total = all.length;
						top = Math.min(top, Math.max(0, total - height));
						window = all.slice(top, top + height);
					}
					const padding = Array.from({ length: Math.max(0, height - window.length) }, () => "");
					const status =
						top === undefined
							? theme.fg("dim", "── fullscreen · PgUp/PgDn scroll · Ctrl+Home top · Ctrl+Alt+F exit")
							: theme.fg(
									"accent",
									`── ↑ rows ${top + 1}–${Math.min(total, top + height)} of ${total} · Ctrl+End or PgDn to the live tail`,
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
					if (editor && !editor.focused) return undefined;
					const page = Math.max(1, height - 2);
					if (matchesKey(data, "pageUp")) scrollBy(-page);
					else if (matchesKey(data, "pageDown")) scrollBy(page);
					else if (matchesKey(data, "ctrl+home")) scrollBy(Number.MIN_SAFE_INTEGER);
					else if (matchesKey(data, "ctrl+end")) scrollBy(Number.MAX_SAFE_INTEGER);
					else return undefined;
					return { consume: true };
				}),
			);
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

export default function fullscreen(pi: ExtensionAPI): void {
	pi.registerCommand("fullscreen", {
		description: "Toggle the fullscreen transcript viewport (alternate screen, composer pinned)",
		handler: async (_args, ctx) => toggle(ctx),
	});
	pi.registerShortcut(TOGGLE_KEY, {
		description: "Toggle the fullscreen transcript viewport",
		handler: ctx => toggle(ctx),
	});
	pi.on("session_start", async (_event, ctx) => {
		if (process.env.OMP_FULLSCREEN === "1") open(ctx);
	});
}
