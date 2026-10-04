// Stand-in text operations for viewport-selection tests, plus the row
// generator and the extraction reference the tests share. viewport.ts passes
// pi-tui's functions; these follow pi-tui's native `sliceWithWidth` rules
// (crates/pi-natives/src/text.rs, `slice_with_width_impl`) for SGR, OSC 8, wide
// characters and OSC 66 text sizing, which Bun alone counts as zero cells.
// APC image rows need pi-tui's own functions.
//
// Proven against omp 18.4.2-sami.20260928-150230 (fork branch `sami` at
// b8fd1d7a): a throwaway parity probe run inside omp found native
// `sliceWithWidth` equal to `standInSlice` on 710 of 710 rows (22 fixed, 88
// OSC 66, 600 generated). Nothing re-checks that: re-run the parity probe
// whenever pi-tui's slicing changes, or these tests go on passing against a
// stale model. `stripTerminalSequences` here is Bun's, which also strips DCS
// and every CSI where pi-tui's does not; the rows where that differs are image
// rows, which the selection leaves alone (`isImageLine`).
import type { TextOps } from "../viewport-selection";
import { random } from "./transcript-fixtures";

const GRAPHEMES = new Intl.Segmenter(undefined, { granularity: "grapheme" });
const ESC = "\x1b";
const TEXT_SIZED = /^\x1b\]66;([^;]*);([\s\S]*)(?:\x1b\\|\x07)$/;

/** Plain cell width of text without escapes, grapheme by grapheme. */
function plainWidth(text: string): number {
	let width = 0;
	for (const { segment } of GRAPHEMES.segment(text)) width += Bun.stringWidth(segment);
	return width;
}

/** An OSC 66 sequence's payload, scale and cells, as the native `osc66_info_u16` reads them. */
function textSized(sequence: string): { payload: string; scale: number; width: number } | undefined {
	const match = TEXT_SIZED.exec(sequence);
	if (!match) return undefined;
	let scale = 1;
	let explicit: number | undefined;
	for (const part of match[1]!.split(":")) {
		const [key, value] = part.split("=");
		const parsed = Number(value);
		if (key === "s" && Number.isInteger(parsed) && parsed >= 1 && parsed <= 7) scale = parsed;
		if (key === "w" && Number.isInteger(parsed) && parsed > 0) explicit = parsed;
	}
	const payload = match[2]!;
	return { payload, scale, width: scale * (explicit ?? plainWidth(payload)) };
}

/** Length of the escape sequence at `i`, as the native `ansi_seq_len_u16` measures it; undefined when none. */
function escapeLength(line: string, i: number): number | undefined {
	const next = line.charCodeAt(i + 1);
	if (Number.isNaN(next)) return undefined;
	const scan = (from: number, done: (code: number, at: number) => number | undefined): number | undefined => {
		for (let at = from; at < line.length; at++) {
			const end = done(line.charCodeAt(at), at);
			if (end !== undefined) return end - i;
		}
		return undefined;
	};
	const stringEnd = (code: number, at: number) =>
		code === 0x07 ? at + 1 : code === 0x1b && line.charCodeAt(at + 1) === 0x5c ? at + 2 : undefined;
	if (next === 0x5b) return scan(i + 2, (code, at) => (code >= 0x40 && code <= 0x7e ? at + 1 : undefined));
	if (next === 0x5d || next === 0x50 || next === 0x58 || next === 0x5e || next === 0x5f) return scan(i + 2, stringEnd);
	if (next >= 0x20 && next <= 0x2f) return scan(i + 2, (code, at) => (code >= 0x30 && code <= 0x7e ? at + 1 : undefined));
	if (next >= 0x40 && next <= 0x7e) return 2;
	return undefined;
}

type Token =
	| { kind: "lone" }
	| { kind: "escape"; text: string; sized?: { payload: string; scale: number; width: number } }
	| { kind: "grapheme"; text: string; width: number };

const tokenCache = new Map<string, Token[]>();

/** Escapes (OSC 66 ones measured), lone ESC bytes, and graphemes with their widths; memoized per line. */
function tokens(line: string): Token[] {
	let cached = tokenCache.get(line);
	if (cached) return cached;
	cached = [];
	let i = 0;
	while (i < line.length) {
		if (line[i] === ESC) {
			const length = escapeLength(line, i);
			if (length === undefined) {
				cached.push({ kind: "lone" });
				i++;
			} else {
				const text = line.slice(i, i + length);
				cached.push({ kind: "escape", text, sized: textSized(text) });
				i += length;
			}
			continue;
		}
		let end = i;
		while (end < line.length && line[end] !== ESC) end++;
		for (const { segment } of GRAPHEMES.segment(line.slice(i, end))) {
			cached.push({ kind: "grapheme", text: segment, width: Bun.stringWidth(segment) });
		}
		i = end;
	}
	if (tokenCache.size > 4096) tokenCache.clear();
	tokenCache.set(line, cached);
	return cached;
}

/**
 * The native slicing rules: escapes met before `startCol` are queued and
 * flushed before the first in-range grapheme; a grapheme that begins before
 * `startCol` is dropped; with `strict`, one that crosses the end is dropped;
 * the escapes right after the text run that reached the end are copied, OSC 66
 * ones skipped; and a zero-length slice is empty, escapes included. An OSC 66
 * span inside the range is copied whole; one the range cuts yields the plain
 * payload columns the cut covers at its scale (`osc66_payload_range`).
 */
export function standInSlice(line: string, startCol: number, length: number, strict: boolean): { text: string; width: number } {
	if (length === 0) return { text: "", width: 0 };
	const endCol = startCol + length;
	const all = tokens(line);
	let text = "";
	let width = 0;
	let col = 0;
	let pending = "";
	let t = 0;
	const emit = (fragment: string, cells: number) => {
		text += pending + fragment;
		pending = "";
		width += cells;
	};
	while (t < all.length && col < endCol) {
		const token = all[t]!;
		if (token.kind === "lone") {
			if (col >= startCol) text += ESC;
			t++;
			continue;
		}
		if (token.kind === "escape") {
			t++;
			const sized = token.sized;
			if (sized === undefined) {
				if (col >= startCol) text += token.text;
				else pending += token.text;
				continue;
			}
			const spanEnd = col + sized.width;
			if (col >= startCol && spanEnd <= endCol) emit(token.text, sized.width);
			else if (col < endCol && spanEnd > startCol) {
				const cutStart = Math.max(0, startCol - col);
				const cutEnd = Math.min(spanEnd, endCol) - col;
				const from = strict ? Math.ceil(cutStart / sized.scale) : Math.floor(cutStart / sized.scale);
				const to = strict ? Math.floor(cutEnd / sized.scale) : Math.ceil(cutEnd / sized.scale);
				let payloadCol = 0;
				for (const { segment } of GRAPHEMES.segment(sized.payload)) {
					if (payloadCol >= to) break;
					const cells = Bun.stringWidth(segment);
					if (payloadCol >= from && (!strict || payloadCol + cells <= to)) emit(segment, cells);
					payloadCol += cells;
				}
			}
			col = spanEnd;
			continue;
		}
		// One text run: the native loop consumes it whole even when the end falls inside it.
		for (; t < all.length; t++) {
			const grapheme = all[t]!;
			if (grapheme.kind !== "grapheme") break;
			if (col >= endCol) continue;
			if (col >= startCol && (!strict || col + grapheme.width <= endCol)) emit(grapheme.text, grapheme.width);
			col += grapheme.width;
		}
	}
	for (; t < all.length; t++) {
		const token = all[t]!;
		if (token.kind !== "escape") break;
		if (token.sized === undefined) text += token.text;
	}
	return { text, width };
}

export const standInOps: TextOps = {
	visibleWidth: text => {
		let width = 0;
		for (const token of tokens(text)) {
			if (token.kind === "grapheme") width += token.width;
			else if (token.kind === "escape" && token.sized) width += token.sized.width;
		}
		return width;
	},
	sliceWithWidth: standInSlice,
	stripTerminalSequences: text => Bun.stripANSI(text),
	// pi-tui's `TERMINAL.isImageLine` under the Kitty protocol, which omp uses
	// inside tmux: an `ESC _ G` graphics APC, or a U+10EEEE placeholder cell, in
	// the row's first 512 code units.
	isImageLine: line => {
		const head = line.slice(0, 512);
		return head.includes("\x1b_G") || head.includes("\u{10eeee}");
	},
};

const WORDS = ["alpha", "b", "src/file.ts:12", "x=1", "(call)", "│", ",", "漢字", "漢", "wide漢b", "  ", " "];
const STYLES = ["\x1b[31m", "\x1b[1;32m", "\x1b[38;5;208m", "\x1b[4m", "\x1b[2m"];
const RESETS = ["\x1b[0m", "\x1b[39m", "\x1b[22m"];
const LINKS: Array<[string, string]> = [
	["\x1b]8;;https://x.io/a?b=1\x1b\\", "\x1b]8;;\x1b\\"],
	["\x1b]8;;https://x.io/c\x07", "\x1b]8;;\x07"],
];

/**
 * Seeded styled rows no wider than `width`: ASCII, CJK, delimiters, SGR runs,
 * OSC 8 links with ST and BEL terminators, and empty rows.
 */
export function generatedRows(seed: number, count: number, width: number): string[] {
	const next = random(seed);
	const pick = <T>(list: readonly T[]): T => list[Math.floor(next() * list.length)]!;
	return Array.from({ length: count }, () => {
		if (next() < 0.1) return "";
		let row = "";
		let used = 0;
		for (;;) {
			const word = pick(WORDS);
			const cells = Bun.stringWidth(word);
			if (used + cells > width) break;
			const style = next();
			if (style < 0.25) row += `${pick(STYLES)}${word}${pick(RESETS)}`;
			else if (style < 0.4) {
				const [open, close] = pick(LINKS);
				row += `${open}${word}${close}`;
			} else if (style < 0.5) row += `${pick(STYLES)}${word}`;
			else row += word;
			used += cells;
			if (next() < 0.08) break;
		}
		return next() < 0.3 ? `${row}\x1b[0m` : row;
	});
}

/**
 * Expected text of columns [from, to] of `row`, computed from the stripped
 * row by cells: a start inside a wide character moves to its first cell, an
 * end inside one moves to its last cell, and trailing spaces are trimmed.
 */
export function referenceText(row: string, from: number, to: number): string {
	const cells: Array<{ start: number; end: number; text: string }> = [];
	let col = 0;
	for (const { segment } of GRAPHEMES.segment(Bun.stripANSI(row))) {
		const width = Bun.stringWidth(segment);
		cells.push({ start: col, end: col + width, text: segment });
		col += width;
	}
	let start = from;
	let end = to;
	for (const cell of cells) {
		if (cell.start < from && from < cell.end) start = cell.start;
		if (cell.start <= to && to < cell.end) end = cell.end - 1;
	}
	return cells
		.filter(cell => cell.start >= start && cell.end - 1 <= end)
		.map(cell => cell.text)
		.join("")
		.trimEnd();
}
