// Mouse text selection for the transcript viewport (viewport.ts), everything
// that touches no omp state. Per row: snapping a column to a whole character,
// word and row units, the plain text of a column range, the reverse-video
// highlight, extending a drag by units, and click counting. Per frame, over
// transcript positions (viewport-window.ts): the unit under the pointer, a
// selection resolved into a range of rows, the columns each row contributes,
// the painted rows, the copied text, and the snapshot that notices a selected
// block re-rendering. viewport.ts keeps only the wiring: mouse dispatch, the
// timers, the footer notice, the clipboard write and render requests.
//
// It imports only viewport-window.ts, itself pure. The text operations arrive
// as parameters: viewport.ts passes pi-tui's `visibleWidth`, `sliceWithWidth`
// and `stripTerminalSequences`, which count omp's text-sizing (OSC 66) and
// image (APC) sequences correctly; the tests pass Bun-based stand-ins that
// follow the same slicing rules.
//
// Everything here leans on pi-tui's native slice semantics: escapes met before
// the start are replayed ahead of the first character taken, a character that
// begins before the start is dropped, with `strict` one that crosses the end is
// dropped, and the escapes right after the end are copied. Columns are snapped
// to character boundaries first, so no slice drops a character. The copied
// trailing escapes mean a highlight's prefix can carry the selected text's own
// opening style ahead of the reverse video, which only tints it.
//
// The row functions are ported from two open upstream omp PRs in the
// MIT-licensed oh-my-pi repo: #10793
// `packages/coding-agent/src/modes/components/viewport-selection.ts`
// (641-git641/oh-my-pi@a0f0b1b4) and #13285
// `packages/tui/src/prompt/transcript-scroll.ts` (affaffaff/oh-my-pi@ee1697b0).
import { type Blocks, type Position, positionFromTail, resolve, rowsBetween, windowWithPositions } from "./viewport-window";

/** Text operations the module needs; viewport.ts passes pi-tui's, tests pass Bun-based ones. */
export interface TextOps {
	visibleWidth(text: string): number;
	sliceWithWidth(line: string, startCol: number, length: number, strict: boolean): { text: string; width: number };
	stripTerminalSequences(text: string): string;
	/** Whether a row carries an image (pi-tui's `TERMINAL.isImageLine`); such rows are neither highlighted nor copied. */
	isImageLine(line: string): boolean;
}

export type Unit = "char" | "word" | "line";

/** OSC 66 text sizing, `ESC ] 66 ; <meta> ; <text>` ended by ST or BEL; its text is visible. */
const TEXT_SIZED = /\x1b\]66;[^;\x07\x1b]*;([^\x07\x1b]*)(?:\x1b\\|\x07)/g;
/** Characters that end a word besides whitespace: #13285's set (transcript-scroll.ts:38-42). */
const WORD_DELIMITER = /^["'`()[\]{}<>,;|│]$/u;

/** Plain visible text of a styled fragment; OSC 66 text-sizing keeps its text. */
export function plainText(ops: TextOps, fragment: string): string {
	return ops.stripTerminalSequences(fragment.replace(TEXT_SIZED, "$1"));
}

/**
 * Each character of `line`, left to right: its first cell, its cell count and
 * its styled slice. #10793 viewport-selection.ts:23-27 measures a character
 * with a strict one-cell slice, retried with two cells when a wide character
 * makes that empty. An OSC 66 text-sized span counts as one character, taken
 * whole as a wide one is: native slicing keeps its sizing only for a slice
 * that holds all of it, and cuts one into plain columns otherwise.
 */
function* characters(ops: TextOps, line: string): Generator<{ from: number; span: number; text: string }> {
	const lineWidth = ops.visibleWidth(line);
	const sized = new Map<number, number>();
	for (const match of line.matchAll(TEXT_SIZED)) {
		const cells = ops.visibleWidth(match[0]);
		if (cells > 0) sized.set(ops.visibleWidth(line.slice(0, match.index)), cells);
	}
	for (let cursor = 0; cursor < lineWidth; ) {
		const whole = sized.get(cursor);
		let slice = ops.sliceWithWidth(line, cursor, whole ?? 1, true);
		if (slice.width === 0) slice = ops.sliceWithWidth(line, cursor, 2, true);
		const span = Math.max(1, Math.min(whole ?? slice.width, lineWidth - cursor));
		yield { from: cursor, span, text: slice.text };
		cursor += span;
	}
}

/** Snap `col` to the first cell (or, with `expandRight`, the last cell) of the character under it. */
export function snapToCell(ops: TextOps, line: string, col: number, expandRight: boolean): number {
	// #10793 viewport-selection.ts:9-33 (`normalizeSelectionPoint`), on one row.
	// A row's first cell starts a character, and the character holding its last
	// cell ends there, so both answer without walking the row.
	if (!expandRight && col <= 0) return col;
	if (col >= ops.visibleWidth(line) - (expandRight ? 1 : 0)) return col;
	for (const { from, span } of characters(ops, line)) {
		if (col < from + span) return expandRight ? from + span - 1 : from;
	}
	return col;
}

/**
 * Inclusive columns of the word under `col`: a run of word characters, a run
 * of whitespace, or a single delimiter, grouped as opentui's
 * text-buffer-view.zig:903-926 groups them. The blank cells past the row's
 * text are one whitespace run to the row end.
 */
function wordColumns(ops: TextOps, line: string, col: number, width: number): { from: number; to: number } {
	const lineWidth = ops.visibleWidth(line);
	const cells: Array<{ from: number; to: number; kind: "space" | "delimiter" | "word" }> = [];
	for (const { from, span, text } of characters(ops, line)) {
		const plain = plainText(ops, text);
		const kind = !/\S/u.test(plain) ? "space" : WORD_DELIMITER.test(plain) ? "delimiter" : "word";
		cells.push({ from, to: from + span - 1, kind });
	}
	if (lineWidth < width) cells.push({ from: lineWidth, to: width - 1, kind: "space" });
	const hit = cells.findIndex(cell => col >= cell.from && col <= cell.to);
	if (hit < 0) return { from: col, to: col };
	const { kind } = cells[hit]!;
	if (kind === "delimiter") return { from: cells[hit]!.from, to: cells[hit]!.to };
	let first = hit;
	let last = hit;
	while (first > 0 && cells[first - 1]!.kind === kind) first--;
	while (last < cells.length - 1 && cells[last + 1]!.kind === kind) last++;
	return { from: cells[first]!.from, to: cells[last]!.to };
}

/** Inclusive columns of `unit` under `col` on one row: a word by delimiters, a line as 0..width-1, a char as the snapped character. */
export function unitColumns(ops: TextOps, line: string, col: number, unit: Unit, width: number): { from: number; to: number } {
	if (unit === "line") return { from: 0, to: width - 1 };
	if (unit === "word") return wordColumns(ops, line, col, width);
	return { from: snapToCell(ops, line, col, false), to: snapToCell(ops, line, col, true) };
}

/** Plain text of columns [from, to] (inclusive, snapped) of one row, trailing spaces trimmed. */
export function rowText(ops: TextOps, line: string, from: number, to: number): string {
	// After #10793 viewport-selection.ts:73-80, plus the trim.
	const start = snapToCell(ops, line, from, false);
	const end = snapToCell(ops, line, to, true);
	if (end < start) return "";
	return plainText(ops, ops.sliceWithWidth(line, start, end - start + 1, true).text).trimEnd();
}

/**
 * `line` with columns [from, to] (inclusive) in reverse video. Both ends snap
 * first; the selected fragment's own escapes are stripped, except OSC 66
 * text sizing, so sized text keeps its cells.
 */
export function highlightRow(ops: TextOps, line: string, from: number, to: number, width: number): string {
	// #10793 viewport-selection.ts:52-53 (snap) and :100-110 (split). The
	// selected fragment loses its own styles, so a reset inside it can't
	// cancel the reverse video (#10793's `stripAnsiPreservingOsc66`); the suffix
	// slice replays the style in force.
	const start = snapToCell(ops, line, from, false);
	const end = Math.min(width - 1, snapToCell(ops, line, to, true));
	if (end < start) return line;
	const prefix = ops.sliceWithWidth(line, 0, start, true).text;
	const selected = ops.sliceWithWidth(line, start, end - start + 1, true).text;
	const suffix = ops.sliceWithWidth(line, end + 1, Math.max(0, width - end - 1), true).text;
	let unstyled = "";
	let last = 0;
	for (const match of selected.matchAll(TEXT_SIZED)) {
		unstyled += ops.stripTerminalSequences(selected.slice(last, match.index)) + match[0];
		last = match.index + match[0].length;
	}
	unstyled += ops.stripTerminalSequences(selected.slice(last));
	return `${prefix}\x1b[7m${unstyled}\x1b[27m${suffix}`;
}

/**
 * A drag's selection, extended by whole units: generic over the point type,
 * ordered by `compare`. #13285 transcript-scroll.ts:226-229: a head at or after
 * the anchor's start keeps the anchor's start and reaches the later end; a head
 * before it keeps the anchor's end.
 */
export function extendSpan<P>(
	anchor: { start: P; end: P },
	head: { start: P; end: P },
	compare: (a: P, b: P) => number,
): { start: P; end: P } {
	if (compare(head.start, anchor.start) >= 0) {
		return { start: anchor.start, end: compare(head.end, anchor.end) >= 0 ? head.end : anchor.end };
	}
	return { start: head.start, end: anchor.end };
}

/** Click counting: a press within MULTI_CLICK_MS on the same row, ±1 column, steps char → word → line → char. */
export interface ClickState {
	at: number;
	row: number;
	col: number;
	count: number;
}

const MULTI_CLICK_MS = 400;

/** The click state after a press at `now` on (`row`, `col`); #13285 transcript-scroll.ts:236-248. */
export function nextClick(previous: ClickState | undefined, now: number, row: number, col: number): ClickState {
	const repeat =
		previous !== undefined &&
		now - previous.at <= MULTI_CLICK_MS &&
		previous.row === row &&
		Math.abs(previous.col - col) <= 1;
	return { at: now, row, col, count: repeat ? (previous.count % 3) + 1 : 1 };
}

export function unitFor(count: number): Unit {
	return count === 2 ? "word" : count === 3 ? "line" : "char";
}

// ---------------------------------------------------------------------------
// Selection over transcript positions
// ---------------------------------------------------------------------------

/** A unit under the pointer: the position of the drawn row it sits on, and its inclusive columns there. */
export interface UnitSpan<B> {
	pos: Position<B>;
	from: number;
	to: number;
}

/** A selection in transcript terms: its ends are positions, so it stays on its text through scrolling and new output. */
export interface Selection<B> {
	/** The unit under the press. */
	anchor: UnitSpan<B>;
	/** The unit under the pointer now. */
	focus: UnitSpan<B>;
	unit: Unit;
	/** The render width it was made at; a render at another width clears it. */
	width: number;
	/** Rows of each covered block at release, keyed by block, for the streaming check. */
	snapshot?: Map<B, readonly string[]>;
}

/** The transcript rows a frame drew, each with the position it came from, below `padding` blank lines. */
export interface Frame<B> {
	rows: readonly string[];
	positions: readonly Position<B>[];
	padding: number;
}

/** A selection resolved against the current blocks: its first and last rows, and the columns it starts and ends at. */
export interface Range<B> {
	start: Position<B>;
	startCol: number;
	end: Position<B>;
	endCol: number;
	/** A char press whose pointer never left its character: nothing is selected. */
	empty: boolean;
}

/**
 * The frame a view `height` rows tall draws from `pos`, or at the live tail
 * when `pos` is undefined: renderTail's composition over the full-size blocks.
 */
export function frameAt<B>(blocks: Blocks<B>, pos: Position<B> | undefined, height: number): Frame<B> {
	const first = pos ?? positionFromTail(blocks, 0, height);
	const { rows, positions } = first === undefined ? { rows: [], positions: [] } : windowWithPositions(blocks, first, height);
	return { rows, positions, padding: Math.max(0, height - rows.length) };
}

/**
 * The `unit` under screen line `row` and column `col` of `frame`. A padding
 * line clamps to the first drawn row, a line below the rows to the last, and
 * the column into the width.
 */
export function spanAt<B>(ops: TextOps, frame: Frame<B>, row: number, col: number, unit: Unit, width: number): UnitSpan<B> | undefined {
	if (frame.rows.length === 0) return undefined;
	const line = Math.min(frame.rows.length - 1, Math.max(0, row - frame.padding));
	const { from, to } = unitColumns(ops, frame.rows[line]!, Math.min(Math.max(0, col), width - 1), unit, width);
	return { pos: frame.positions[line]!, from, to };
}

/**
 * The column a drag's focus takes on screen line `row`: the row end at or below
 * the status row (line `height`), and column 0 on the top line once the drag has
 * moved vertically, so edge scrolling takes whole rows; otherwise the pointer's.
 */
export function pinnedColumn(row: number, col: number, movedVertically: boolean, height: number, width: number): number {
	if (row >= height) return width - 1;
	return movedVertically && row <= 0 ? 0 : col;
}

/**
 * `selection` resolved against `blocks`: its ends ordered and extended by whole
 * units (extendSpan). Undefined once either end's block has left the list, or
 * the list was replaced.
 */
export function resolveSelection<B>(blocks: Blocks<B>, selection: Selection<B>): Range<B> | undefined {
	const anchor = resolve(blocks, selection.anchor.pos);
	const focus = resolve(blocks, selection.focus.pos);
	if (anchor?.block !== selection.anchor.pos.block || focus?.block !== selection.focus.pos.block) return undefined;
	const compare = (a: { pos: Position<B>; col: number }, b: { pos: Position<B>; col: number }) =>
		a.pos.index - b.pos.index || a.pos.offset - b.pos.offset || a.col - b.col;
	const anchorStart = { pos: anchor, col: selection.anchor.from };
	const focusStart = { pos: focus, col: selection.focus.from };
	const span = extendSpan(
		{ start: anchorStart, end: { pos: anchor, col: selection.anchor.to } },
		{ start: focusStart, end: { pos: focus, col: selection.focus.to } },
		compare,
	);
	return {
		start: span.start.pos,
		startCol: span.start.col,
		end: span.end.pos,
		endCol: span.end.col,
		empty: selection.unit === "char" && compare(focusStart, anchorStart) === 0,
	};
}

/**
 * The columns the row at `pos` contributes to `range`: from the start column on
 * its first row, to the end column on its last, the whole row between, and
 * undefined outside it. `pos` is resolved against the same blocks as `range`.
 */
export function selectedColumns<B>(range: Range<B>, pos: Position<B>, width: number): { from: number; to: number } | undefined {
	const afterStart = pos.index - range.start.index || pos.offset - range.start.offset;
	const beforeEnd = range.end.index - pos.index || range.end.offset - pos.offset;
	if (afterStart < 0 || beforeEnd < 0) return undefined;
	return { from: afterStart === 0 ? range.startCol : 0, to: beforeEnd === 0 ? range.endCol : width - 1 };
}

/**
 * The frame's rows with `range` in reverse video. An image row is left as it
 * is: its escapes would be stripped with the selected text, which leaves a
 * tmux-wrapped placement unterminated and swallows the rest of the frame, as
 * pi-tui's own compositor avoids for partial overlays.
 */
export function paintRows<B>(ops: TextOps, frame: Frame<B>, range: Range<B>, width: number): readonly string[] {
	if (range.empty) return frame.rows;
	return frame.rows.map((row, i) => {
		const columns = selectedColumns(range, frame.positions[i]!, width);
		return columns === undefined || ops.isImageLine(row) ? row : highlightRow(ops, row, columns.from, columns.to, width);
	});
}

/**
 * The text `range` covers, each row's selected columns as plain text joined by
 * newlines, and its row count. An image row copies as an empty line, so the
 * count still includes it.
 */
export function copyText<B>(ops: TextOps, blocks: Blocks<B>, range: Range<B>, width: number): { text: string; lines: number } {
	const { rows, positions } = rowsBetween(blocks, range.start, range.end);
	const text = rows
		.map((row, i) => {
			if (ops.isImageLine(row)) return "";
			// rowsBetween returns only rows from the start through the end.
			const { from, to } = selectedColumns(range, positions[i]!, width)!;
			return rowText(ops, row, from, to);
		})
		.join("\n");
	return { text, lines: rows.length };
}

/** A copy of the rows of every block `range` covers, keyed by block. */
export function snapshotRows<B>(blocks: Blocks<B>, range: Range<B>): Map<B, readonly string[]> {
	const snapshot = new Map<B, readonly string[]>();
	for (let index = range.start.index; index <= range.end.index; index++) {
		const block = blocks.at(index);
		// A copy: a renderer may hand back the same array, mutated, next time.
		snapshot.set(block, blocks.rows(block).slice());
	}
	return snapshot;
}

/**
 * Whether a drawn row of a snapshotted block differs from the snapshot's row at
 * the same offset; a separator row compares as "". Only drawn rows count.
 */
export function snapshotChanged<B>(frame: Frame<B>, snapshot: Map<B, readonly string[]>): boolean {
	return frame.rows.some((row, i) => {
		const { block, offset } = frame.positions[i]!;
		const rows = snapshot.get(block);
		return rows !== undefined && row !== (offset < rows.length ? rows[offset] : "");
	});
}
