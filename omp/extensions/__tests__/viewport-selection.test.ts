import { describe, expect, test } from "bun:test";
import {
	type ClickState,
	copyText,
	extendSpan,
	frameAt,
	highlightRow,
	nextClick,
	paintRows,
	pinnedColumn,
	plainText,
	resolveSelection,
	rowText,
	type Selection,
	selectedColumns,
	snapshotChanged,
	snapshotRows,
	snapToCell,
	spanAt,
	type Unit,
	type UnitSpan,
	unitColumns,
	unitFor,
} from "../viewport-selection";
import { type Blocks, type Position, top, trimBlankEdges, windowWithPositions } from "../viewport-window";
import { generatedRows, referenceText, standInOps as ops } from "./selection-stand-in";
import { type Block, blocksOf, compose, random, transcript } from "./transcript-fixtures";

/** Seeded property tests: bun's default 5 s per test is too short for them on a loaded box. */
const PROPERTY_TIMEOUT_MS = 30_000;

const WIDTH = 40;
const ROWS = generatedRows(7919, 160, WIDTH);

/** Seeded inclusive column pairs, `from <= to`, inside the width. */
function pairs(seed: number, count: number): Array<[number, number]> {
	const next = random(seed);
	return Array.from({ length: count }, () => {
		const a = Math.floor(next() * WIDTH);
		const b = Math.floor(next() * WIDTH);
		return a <= b ? [a, b] : [b, a];
	});
}

/** The text between a highlight's reverse-video markers; undefined when the line has none. */
function reversed(line: string): string | undefined {
	const open = line.indexOf("\x1b[7m");
	return open < 0 ? undefined : line.slice(open + 4, line.indexOf("\x1b[27m", open));
}

/** Fixture transcripts whose rows are the stand-in's styled rows: SGR, OSC 8, CJK, delimiters. */
function styledTranscript(count: number, seed: number, emptyRate: number): Block[] {
	const pool = generatedRows(seed, 64, WIDTH).filter(row => row !== "");
	return transcript(count, seed, emptyRate, (id, r) => pool[(id * 5 + r) % pool.length]!);
}

/** The composed rows of `list` and the position of each, top to bottom. */
function drawnTranscript(list: Block[], blocks: Blocks<Block> = blocksOf(list)) {
	const ref = compose(list);
	const first = top(blocks);
	const positions = first === undefined ? [] : windowWithPositions(blocks, first, ref.length).positions;
	return { blocks, ref, positions };
}

/** The `unit` under column `col` of composed row `row`. */
function spanOn(ref: readonly string[], positions: readonly Position<Block>[], row: number, col: number, unit: Unit = "char"): UnitSpan<Block> {
	return { pos: positions[row]!, ...unitColumns(ops, ref[row]!, col, unit, WIDTH) };
}

type Point = { row: number; col: number };
const comparePoints = (a: Point, b: Point) => (a.row === b.row ? a.col - b.col : a.row - b.row);

describe("viewport-selection", () => {
	test("rowText extracts exactly the reference text of any column range", () => {
		ROWS.forEach((row, index) => {
			for (const [from, to] of pairs(index + 1, 24)) {
				expect(rowText(ops, row, from, to), `${JSON.stringify(row)} [${from}, ${to}]`).toBe(referenceText(row, from, to));
			}
		});
	}, PROPERTY_TIMEOUT_MS);

	test("a highlighted row strips back to the original row, with one reverse-video span", () => {
		ROWS.forEach((row, index) => {
			for (const [from, to] of pairs(index + 1, 24)) {
				const lit = highlightRow(ops, row, from, to, WIDTH);
				const name = `${JSON.stringify(row)} [${from}, ${to}]`;
				expect(Bun.stripANSI(lit), name).toBe(Bun.stripANSI(row));
				expect(lit.split("\x1b[7m").length - 1, name).toBe(1);
				expect(lit.indexOf("\x1b[27m", lit.indexOf("\x1b[7m")), name).toBeGreaterThan(-1);
			}
		});
	}, PROPERTY_TIMEOUT_MS);

	test("a wide character at a highlight edge is taken whole", () => {
		for (const [from, to] of [
			[2, 3],
			[0, 2],
			[0, 1],
		] as const) {
			const lit = highlightRow(ops, "a漢b", from, to, 4);
			expect(Bun.stripANSI(lit), `[${from}, ${to}]`).toBe("a漢b");
			expect(reversed(lit), `[${from}, ${to}]`).toContain("漢");
		}
	});

	test("an embedded reset cannot cancel the highlight", () => {
		const lit = highlightRow(ops, "\x1b[31mab\x1b[0mcd", 0, 3, 10);
		expect(reversed(lit)).toBe("abcd");
		expect(reversed(lit)).not.toContain("\x1b");
	});

	test("a text-sized (OSC 66) span is taken whole, and a highlight keeps it sized", () => {
		const title = `\x1b]66;s=2;Title\x1b\\ rest`;
		expect(snapToCell(ops, title, 3, false)).toBe(0);
		expect(snapToCell(ops, title, 3, true)).toBe(9);
		expect(rowText(ops, title, 2, 4)).toBe("Title");
		expect(unitColumns(ops, title, 12, "word", WIDTH)).toEqual({ from: 11, to: 14 });
		for (const row of [title, "# \x1b[1m\x1b]66;s=2;Hi there\x07\x1b[0m end"]) {
			const width = ops.visibleWidth(row) + 2;
			for (let from = 0; from < width; from++) {
				for (let to = from; to < width; to++) {
					const lit = highlightRow(ops, row, from, to, width);
					expect(plainText(ops, lit), `${JSON.stringify(row)} [${from}, ${to}]`).toBe(plainText(ops, row));
					expect(ops.visibleWidth(lit), `${JSON.stringify(row)} [${from}, ${to}]`).toBe(ops.visibleWidth(row));
				}
			}
		}
	}, PROPERTY_TIMEOUT_MS);

	test("snapToCell moves to the first or last cell of the character under the column", () => {
		expect(snapToCell(ops, "a漢b", 2, false)).toBe(1);
		expect(snapToCell(ops, "a漢b", 1, true)).toBe(2);
		expect(snapToCell(ops, "a漢b", 1, false)).toBe(1);
		expect(snapToCell(ops, "a漢b", 3, true)).toBe(3);
	});

	test("a word ends at whitespace and delimiters, and keeps paths and URLs whole", () => {
		const word = (line: string, col: number) => unitColumns(ops, line, col, "word", WIDTH);
		const span = (line: string, text: string) => ({ from: line.indexOf(text), to: line.indexOf(text) + text.length - 1 });
		const path = "see /home/u/src/file.ts:12, then";
		expect(word(path, 10)).toEqual(span(path, "/home/u/src/file.ts:12"));
		expect(word(path, 4)).toEqual(span(path, "/home/u/src/file.ts:12"));
		const url = "https://x.io/a?b=1&c=2)";
		expect(word(url, 7)).toEqual(span(url, "https://x.io/a?b=1&c=2"));
		expect(word("foo(bar)", 5)).toEqual({ from: 4, to: 6 });
		expect(word("\x1b[1mfoo\x1b[0m(\x1b[32mbar\x1b[0m)", 4)).toEqual({ from: 4, to: 6 });
		expect(word("a   b", 2)).toEqual({ from: 1, to: 3 });
		expect(word("ab  ", 10)).toEqual({ from: 2, to: WIDTH - 1 });
		expect(word("a \x1b[2m│\x1b[0m b", 2)).toEqual({ from: 2, to: 2 });
		expect(word("a││b", 1)).toEqual({ from: 1, to: 1 });
		expect(word("漢字 ok", 1)).toEqual({ from: 0, to: 3 });
	});

	test("a line unit is the whole row", () => {
		expect(unitColumns(ops, "short", 2, "line", WIDTH)).toEqual({ from: 0, to: WIDTH - 1 });
		expect(unitColumns(ops, "", 0, "line", 7)).toEqual({ from: 0, to: 6 });
	});

	test("a char unit is the character under the column", () => {
		expect(unitColumns(ops, "a漢b", 2, "char", WIDTH)).toEqual({ from: 1, to: 2 });
		expect(unitColumns(ops, "a漢b", 3, "char", WIDTH)).toEqual({ from: 3, to: 3 });
	});

	test("extending by units keeps the anchor unit and adds the head unit, across rows", () => {
		const anchor = { start: { row: 3, col: 4 }, end: { row: 3, col: 8 } };
		const below = { start: { row: 5, col: 0 }, end: { row: 5, col: 3 } };
		expect(extendSpan(anchor, below, comparePoints)).toEqual({ start: anchor.start, end: below.end });
		const above = { start: { row: 1, col: 2 }, end: { row: 1, col: 6 } };
		expect(extendSpan(anchor, above, comparePoints)).toEqual({ start: above.start, end: anchor.end });
		expect(extendSpan(anchor, anchor, comparePoints)).toEqual(anchor);
		const inside = { start: { row: 3, col: 5 }, end: { row: 3, col: 5 } };
		expect(extendSpan(anchor, inside, comparePoints)).toEqual(anchor);
		const sameRowLeft = { start: { row: 3, col: 0 }, end: { row: 3, col: 2 } };
		expect(extendSpan(anchor, sameRowLeft, comparePoints)).toEqual({ start: sameRowLeft.start, end: anchor.end });
	});

	test("presses within the multi-click window on the same spot step char, word, line, char", () => {
		const presses = (list: Array<[number, number, number]>) => {
			let state: ClickState | undefined;
			return list.map(([at, row, col]) => (state = nextClick(state, at, row, col)).count);
		};
		expect(presses([[0, 4, 10], [100, 4, 10], [200, 4, 10], [300, 4, 10]])).toEqual([1, 2, 3, 1]);
		expect(presses([[0, 4, 10], [450, 4, 10]])).toEqual([1, 1]);
		expect(presses([[0, 4, 10], [400, 4, 10]])).toEqual([1, 2]);
		expect(presses([[0, 4, 10], [100, 4, 11]])).toEqual([1, 2]);
		expect(presses([[0, 4, 10], [100, 4, 9]])).toEqual([1, 2]);
		expect(presses([[0, 4, 10], [100, 4, 12]])).toEqual([1, 1]);
		expect(presses([[0, 4, 10], [100, 5, 10]])).toEqual([1, 1]);
		expect([1, 2, 3].map(unitFor)).toEqual(["char", "word", "line"]);
	});

	test("plainText keeps OSC 66 text-sizing text and drops other escapes", () => {
		expect(plainText(ops, "\x1b]66;s=2;Title\x1b\\ rest")).toBe("Title rest");
		expect(plainText(ops, "\x1b]66;s=2;Title\x07 rest")).toBe("Title rest");
		expect(plainText(ops, "\x1b[1m\x1b]66;s=2:w=5;Ti;tle\x1b\\\x1b[0m")).toBe("Ti;tle");
	});
});

describe("viewport-selection over transcript positions", () => {
	test("frameAt draws renderTail's last rows at the live tail, and spanAt clamps padding and lines below into the rows", () => {
		for (let seed = 1; seed <= 4; seed++) {
			for (const [count, emptyRate] of [[0, 0], [1, 0], [3, 0.3], [9, 0.3]] as const) {
				const list = transcript(count, seed * 104729, emptyRate);
				const { blocks, ref, positions } = drawnTranscript(list);
				for (const height of [1, 3, 8, ref.length + 3]) {
					const tail = frameAt(blocks, undefined, height);
					const first = Math.max(0, ref.length - height);
					expect(tail.rows).toEqual(ref.slice(first));
					expect(tail.positions).toEqual(positions.slice(first));
					expect(tail.padding).toBe(Math.max(0, height - ref.length));
					for (let row = -2; row < height + 3; row++) {
						const span = spanAt(ops, tail, row, WIDTH + 10, "char", WIDTH);
						if (tail.rows.length === 0) {
							expect(span).toBeUndefined();
							continue;
						}
						const line = Math.min(tail.rows.length - 1, Math.max(0, row - tail.padding));
						expect(span).toEqual({ pos: tail.positions[line]!, from: WIDTH - 1, to: WIDTH - 1 });
					}
				}
				for (let row = 0; row < ref.length; row++) {
					const view = windowWithPositions(blocks, positions[row]!, 4);
					expect(frameAt(blocks, positions[row], 4)).toEqual({ rows: view.rows, positions: view.positions, padding: 4 - view.rows.length });
				}
			}
		}
	});

	test("pinnedColumn takes the row end at or below the status row, and column 0 on the top line only after vertical movement", () => {
		const height = 10;
		expect(pinnedColumn(5, 7, true, height, WIDTH)).toBe(7);
		expect(pinnedColumn(0, 7, false, height, WIDTH)).toBe(7);
		expect(pinnedColumn(0, 7, true, height, WIDTH)).toBe(0);
		expect(pinnedColumn(height - 1, 7, true, height, WIDTH)).toBe(7);
		expect(pinnedColumn(height, 7, false, height, WIDTH)).toBe(WIDTH - 1);
		expect(pinnedColumn(height + 4, 7, true, height, WIDTH)).toBe(WIDTH - 1);
	});

	test("copyText and paintRows take exactly the reference cells between two drawn points, in either order", () => {
		const next = random(31337);
		for (let seed = 1; seed <= 6; seed++) {
			for (const emptyRate of [0, 0.4]) {
				const list = styledTranscript(12, seed * 7919, emptyRate);
				const { blocks, ref, positions } = drawnTranscript(list);
				if (ref.length === 0) continue;
				for (let pair = 0; pair < 30; pair++) {
					const a = Math.floor(next() * ref.length);
					const b = Math.floor(next() * ref.length);
					const ca = Math.floor(next() * WIDTH);
					const cb = Math.floor(next() * WIDTH);
					const selection: Selection<Block> = {
						anchor: spanOn(ref, positions, a, ca),
						focus: spanOn(ref, positions, b, cb),
						unit: "char",
						width: WIDTH,
					};
					const range = resolveSelection(blocks, selection)!;
					const name = `seed ${seed} empty ${emptyRate}: (${a},${ca}) to (${b},${cb})`;
					if (range.empty) {
						expect([a, selection.anchor.from], name).toEqual([b, selection.focus.from]);
						continue;
					}
					const [low, lowCol, high, highCol] = a < b || (a === b && ca <= cb) ? [a, ca, b, cb] : [b, cb, a, ca];
					const columnsOf = (row: number): [number, number] => [row === low ? lowCol : 0, row === high ? highCol : WIDTH - 1];
					const expected = ref
						.slice(low, high + 1)
						.map((row, k) => referenceText(row, ...columnsOf(low + k)))
						.join("\n");
					expect(copyText(ops, blocks, range, WIDTH), name).toEqual({ text: expected, lines: high - low + 1 });
					const frameTop = Math.floor(next() * ref.length);
					const frame = frameAt(blocks, positions[frameTop], 1 + Math.floor(next() * 12));
					const painted = paintRows(ops, frame, range, WIDTH);
					frame.rows.forEach((row, k) => {
						const r = frameTop + k;
						if (r < low || r > high) {
							expect(painted[k], `${name} row ${r}`).toBe(row);
							return;
						}
						expect(Bun.stripANSI(painted[k]!), `${name} row ${r}`).toBe(Bun.stripANSI(row));
						expect(reversed(painted[k]!)?.trimEnd(), `${name} row ${r}`).toBe(referenceText(row, ...columnsOf(r)));
					});
				}
			}
		}
	}, PROPERTY_TIMEOUT_MS);

	test("a row unit dragged across rows copies them whole, and a word unit extends by words", () => {
		const list: Block[] = [{ id: 0, rows: ["alpha /home/u/src/file.ts:12, beta", "second row"] }, { id: 1, rows: ["third one"] }];
		const { blocks, ref, positions } = drawnTranscript(list);
		const rows: Selection<Block> = { anchor: spanOn(ref, positions, 3, 2, "line"), focus: spanOn(ref, positions, 0, 9, "line"), unit: "line", width: WIDTH };
		expect(copyText(ops, blocks, resolveSelection(blocks, rows)!, WIDTH)).toEqual({ text: `${ref[0]}\n${ref[1]}\n\n${ref[3]}`, lines: 4 });
		const words: Selection<Block> = { anchor: spanOn(ref, positions, 0, 10, "word"), focus: spanOn(ref, positions, 1, 1, "word"), unit: "word", width: WIDTH };
		expect(copyText(ops, blocks, resolveSelection(blocks, words)!, WIDTH).text).toBe("/home/u/src/file.ts:12, beta\nsecond");
	});

	test("a char press that never left its character is empty, a wide character included; word and row units are not", () => {
		const list: Block[] = [{ id: 0, rows: ["a漢b cd"] }];
		const { blocks, ref, positions } = drawnTranscript(list);
		const at = (col: number, unit: Unit = "char") => spanOn(ref, positions, 0, col, unit);
		const range = (anchor: UnitSpan<Block>, focus: UnitSpan<Block>, unit: Unit = "char") =>
			resolveSelection(blocks, { anchor, focus, unit, width: WIDTH })!;
		expect(range(at(1), at(2)).empty).toBe(true);
		expect(range(at(2), at(1)).empty).toBe(true);
		expect(range(at(1), at(3)).empty).toBe(false);
		expect(range(at(4, "word"), at(4, "word"), "word").empty).toBe(false);
		expect(range(at(0, "line"), at(0, "line"), "line").empty).toBe(false);
	});

	test("selectedColumns gives the start column on the first row, the end column on the last, whole rows between, nothing outside", () => {
		const list: Block[] = [{ id: 0, rows: ["r0", "r1", "r2", "r3"] }];
		const { blocks, ref, positions } = drawnTranscript(list);
		const multi = resolveSelection(blocks, { anchor: spanOn(ref, positions, 1, 1), focus: spanOn(ref, positions, 2, 0), unit: "char", width: WIDTH })!;
		expect(positions.map(pos => selectedColumns(multi, pos, WIDTH))).toEqual([undefined, { from: 1, to: WIDTH - 1 }, { from: 0, to: 0 }, undefined]);
		const single = resolveSelection(blocks, { anchor: spanOn(ref, positions, 3, 1), focus: spanOn(ref, positions, 3, 0), unit: "char", width: WIDTH })!;
		expect(positions.map(pos => selectedColumns(single, pos, WIDTH))).toEqual([undefined, undefined, undefined, { from: 0, to: 1 }]);
	});

	test("when an end's block stops drawing, the copy takes the remaining rows whole, as the highlight does", () => {
		const endGone: Block[] = [{ id: 0, rows: ["alpha row one", "alpha row two long text"] }, { id: 1, rows: ["beta"] }];
		const end = drawnTranscript(endGone);
		const toBeta: Selection<Block> = { anchor: spanOn(end.ref, end.positions, 0, 0), focus: spanOn(end.ref, end.positions, 3, 2), unit: "char", width: WIDTH };
		endGone[1]!.rows = [];
		const range = resolveSelection(end.blocks, toBeta)!;
		expect(copyText(ops, end.blocks, range, WIDTH)).toEqual({ text: "alpha row one\nalpha row two long text", lines: 2 });
		expect(paintRows(ops, frameAt(end.blocks, top(end.blocks), 4), range, WIDTH).map(reversed)).toEqual(["alpha row one", "alpha row two long text"]);

		const startGone: Block[] = [{ id: 0, rows: ["gamma"] }, { id: 1, rows: ["delta row one long", "delta row two"] }];
		const start = drawnTranscript(startGone);
		const selection: Selection<Block> = { anchor: spanOn(start.ref, start.positions, 0, 3), focus: spanOn(start.ref, start.positions, 3, 4), unit: "char", width: WIDTH };
		startGone[0]!.rows = [];
		const fromGamma = resolveSelection(start.blocks, selection)!;
		expect(copyText(ops, start.blocks, fromGamma, WIDTH)).toEqual({ text: "delta row one long\ndelta", lines: 2 });
		expect(paintRows(ops, frameAt(start.blocks, top(start.blocks), 4), fromGamma, WIDTH).map(reversed)).toEqual(["delta row one long", "delta"]);
	});

	test("resolveSelection follows its ends through inserted blocks, and is undefined once either end's block leaves", () => {
		for (const side of ["anchor", "focus"] as const) {
			const list = transcript(30, 17, 0.1);
			const { blocks, ref, positions } = drawnTranscript(list);
			const selection: Selection<Block> = { anchor: spanOn(ref, positions, 3, 0), focus: spanOn(ref, positions, ref.length - 4, 2), unit: "char", width: WIDTH };
			const before = copyText(ops, blocks, resolveSelection(blocks, selection)!, WIDTH);
			list.unshift({ id: -1, rows: ["new"] }, { id: -2, rows: [] });
			list.push({ id: 99, rows: ["tail"] });
			expect(copyText(ops, blocks, resolveSelection(blocks, selection)!, WIDTH)).toEqual(before);
			list.splice(list.indexOf(selection[side].pos.block), 1);
			expect(resolveSelection(blocks, selection), side).toBeUndefined();
		}
		let list = transcript(20, 5, 0.1);
		const replaced: Blocks<Block> = {
			get length() {
				return list.length;
			},
			at: i => list[i]!,
			rows: b => trimBlankEdges(b.rows),
			get source() {
				return list;
			},
		};
		const { ref, positions } = drawnTranscript(list, replaced);
		const selection: Selection<Block> = { anchor: spanOn(ref, positions, 1, 0), focus: spanOn(ref, positions, 4, 0), unit: "char", width: WIDTH };
		list = [...list];
		expect(resolveSelection(replaced, selection)).toBeDefined();
		list = transcript(20, 5, 0.1);
		expect(resolveSelection(replaced, selection)).toBeUndefined();
	});

	test("snapshotChanged notices a drawn row of a covered block changing, and nothing else", () => {
		const list: Block[] = [{ id: 0, rows: ["a0", "a1"] }, { id: 1, rows: ["b0", "b1", "b2"] }, { id: 2, rows: ["c0"] }];
		const { blocks, ref, positions } = drawnTranscript(list);
		const range = resolveSelection(blocks, { anchor: spanOn(ref, positions, 0, 0), focus: spanOn(ref, positions, 4, 1), unit: "char", width: WIDTH })!;
		const snapshot = snapshotRows(blocks, range);
		expect([...snapshot.keys()]).toEqual([list[0]!, list[1]!]);
		const whole = () => frameAt(blocks, top(blocks), 20);
		expect(snapshotChanged(whole(), snapshot)).toBe(false);
		list[2]!.rows[0] = "c0 changed";
		expect(snapshotChanged(whole(), snapshot)).toBe(false);
		// Mutated in place, as a renderer that reuses its row array would: the snapshot is a copy.
		list[1]!.rows[1] = "b1 changed";
		expect(snapshotChanged(frameAt(blocks, top(blocks), 2), snapshot)).toBe(false);
		expect(snapshotChanged(whole(), snapshot)).toBe(true);

		const last: Block[] = [{ id: 0, rows: ["x0"] }, { id: 1, rows: ["y0", "y1"] }];
		const tail = drawnTranscript(last);
		const covered = snapshotRows(tail.blocks, resolveSelection(tail.blocks, { anchor: spanOn(tail.ref, tail.positions, 0, 0), focus: spanOn(tail.ref, tail.positions, 3, 1), unit: "char", width: WIDTH })!);
		last.push({ id: 2, rows: ["z0"] });
		expect(snapshotChanged(frameAt(tail.blocks, top(tail.blocks), 20), covered)).toBe(false);
		last[1]!.rows.push("y2");
		expect(snapshotChanged(frameAt(tail.blocks, top(tail.blocks), 20), covered)).toBe(true);
	});

	test("image rows are neither highlighted nor copied, and still count as copied lines", () => {
		// omp's Kitty image rows under tmux, as pi-tui renders them: a placeholder
		// grid whose first row carries the virtual placement wrapped in a tmux DCS,
		// and a direct placement's last row.
		const cells = (row: number) =>
			[0x0305, 0x030d, 0x030e, 0x0310].map(col => `\u{10eeee}${String.fromCodePoint([0x0305, 0x030d][row]!)}${String.fromCodePoint(col)}`).join("");
		const placeholderFirst = `  \x1bPtmux;\x1b\x1b_Ga=p,U=1,q=2,i=5,c=4,r=2\x1b\x1b\\\x1b\\\x1b[38;2;0;0;5m${cells(0)}\x1b[39;59m`;
		const placeholderNext = `  \x1b[38;2;0;0;5m${cells(1)}\x1b[39;59m`;
		const directLast = "\x1b7\x1b[1A\x1b_Ga=p,i=5,q=2\x1b\\\x1b8";
		for (const image of [[placeholderFirst, placeholderNext], ["\x1b[0m", directLast]]) {
			const list: Block[] = [{ id: 0, rows: ["before image", ...image, "after image"] }];
			const { blocks, ref, positions } = drawnTranscript(list);
			const range = resolveSelection(blocks, { anchor: spanOn(ref, positions, 0, 0), focus: spanOn(ref, positions, 3, 10), unit: "char", width: WIDTH })!;
			const painted = paintRows(ops, frameAt(blocks, top(blocks), 4), range, WIDTH);
			for (const [k, row] of ref.entries()) {
				if (ops.isImageLine(row)) expect(painted[k]).toBe(row);
				else expect(reversed(painted[k]!)?.trimEnd()).toBe(Bun.stripANSI(row));
			}
			expect(copyText(ops, blocks, range, WIDTH)).toEqual({ text: "before image\n\n\nafter image", lines: 4 });
		}
		expect([placeholderFirst, placeholderNext, directLast, "\x1b[0m", "before image"].map(ops.isImageLine)).toEqual([true, true, true, false, false]);
	});
});
