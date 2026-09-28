import { describe, expect, test } from "bun:test";
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
} from "../viewport-window";

type Block = { id: number; rows: string[] };

/** Blocks over `list`, read fresh on every call; `source` is the array, as viewport.ts passes `transcript.children`. */
function blocksOf(list: Block[]): Blocks<Block> {
	return {
		get length() {
			return list.length;
		},
		at: i => list[i]!,
		rows: b => trimBlankEdges(b.rows),
		source: list,
	};
}

/** omp's `TranscriptContainer.renderTail` composition, written forwards. */
function compose(list: Block[]): string[] {
	const out: string[] = [];
	for (const block of list) {
		const rows = trimBlankEdges(block.rows);
		if (rows.length === 0) continue;
		if (out.length > 0) out.push("");
		out.push(...rows);
	}
	return out;
}

/** Rows in each block's unit: its rows plus one separator when a later block draws something. */
function unitLengths(list: Block[]): number[] {
	const lengths = list.map(b => trimBlankEdges(b.rows).length);
	return lengths.map((n, i) => (n === 0 ? 0 : n + (lengths.slice(i + 1).some(m => m > 0) ? 1 : 0)));
}

/** A position's absolute row in `compose(list)`, given `units = unitLengths(list)`. */
function rowOf(units: readonly number[], pos: Position<Block>): number {
	return units.slice(0, pos.index).reduce((a, b) => a + b, 0) + pos.offset;
}

/**
 * Deterministic transcripts: 1-5 row blocks with interior blank rows, blank
 * edges, all-blank blocks and empty blocks at `emptyRate`. Park-Miller keeps
 * every step exact (48271 x (2^31 - 1) < 2^53).
 */
function transcript(count: number, seed: number, emptyRate: number): Block[] {
	let state = seed;
	const next = () => ((state = (state * 48271) % 2147483647) / 2147483647);
	return Array.from({ length: count }, (_, id) => {
		const kind = next();
		if (kind < emptyRate) return { id, rows: [] };
		if (kind < emptyRate + 0.05) return { id, rows: ["   ", "", "\t"] };
		const body = Array.from({ length: 1 + Math.floor(next() * 5) }, (_, r) => (next() < 0.1 ? "" : `b${id}r${r}`));
		body[0] ||= `b${id}first`;
		body[body.length - 1] ||= `b${id}last`;
		return { id, rows: kind < emptyRate + 0.2 ? ["", ...body, " "] : body };
	});
}

const CASES: Array<[string, Block[]]> = [];
// Three seeds keep every test here well inside bun's 5 s per-test limit on a loaded
// machine while still killing every behavior-changing mutant tried against the module.
for (let seed = 1; seed <= 3; seed++) {
	for (const count of [0, 1, 2, 3, 5, 12, 40]) {
		for (const emptyRate of [0, 0.3, 0.7]) CASES.push([`seed ${seed} count ${count} empty ${emptyRate}`, transcript(count, seed * 7919, emptyRate)]);
	}
}

describe("viewport-window", () => {
	test("every view from the tail, and every step from it, shows the rows renderTail would", () => {
		for (const [name, list] of CASES) {
			const ref = compose(list);
			const units = unitLengths(list);
			const blocks = blocksOf(list);
			const total = ref.length;
			if (total === 0) {
				expect(top(blocks), name).toBeUndefined();
				expect(positionFromTail(blocks, 0, 3), name).toBeUndefined();
				continue;
			}
			for (const height of [1, 2, 3, 7, total, total + 2]) {
				for (let back = 0; back <= total + 2; back++) {
					const pos = positionFromTail(blocks, back, height)!;
					const row = rowOf(units, pos);
					expect(row, `${name} h${height} back${back}`).toBe(Math.max(0, total - back - height));
					const view = windowAt(blocks, pos, height);
					expect(view.rows, name).toEqual(ref.slice(row, row + height));
					expect(view.atEnd, name).toBe(row + height >= total);
					expect(isTop(blocks, pos), name).toBe(row === 0);
					for (const step of [0, 1, 2, 3, 5, 11, total]) {
						const up = moveUp(blocks, pos, step);
						expect(rowOf(units, up), `${name} up ${step}`).toBe(Math.max(0, row - step));
						const down = moveDown(blocks, pos, step, height);
						if (row + step + height >= total) {
							expect(down, `${name} h${height} row${row} down ${step}`).toBeUndefined();
						} else {
							expect(rowOf(units, down!), `${name} down ${step}`).toBe(row + step);
							expect(windowAt(blocks, down!, height).rows).toEqual(ref.slice(row + step, row + step + height));
						}
					}
				}
			}
		}
	});

	test("any block and offset, including empty blocks, windows and moves correctly", () => {
		for (let seed = 1; seed < 40; seed++) {
			for (const count of [1, 2, 4, 9, 20]) {
				for (const emptyRate of [0, 0.4, 0.8]) {
					const list = transcript(count, seed * 104729, emptyRate);
					const ref = compose(list);
					const units = unitLengths(list);
					const blocks = blocksOf(list);
					for (let index = 0; index < list.length; index++) {
						for (let offset = 0; offset < Math.max(1, units[index]!); offset++) {
							const pos: Position<Block> = { block: list[index]!, index, offset, source: list };
							const row = rowOf(units, pos);
							for (const height of [1, 3, 6]) {
								expect(windowAt(blocks, pos, height).rows).toEqual(ref.slice(row, row + height));
								expect(windowAt(blocks, resolve(blocks, pos)!, height).rows).toEqual(ref.slice(row, row + height));
								for (const step of [1, 2, 5]) {
									const upRow = Math.max(0, row - step);
									expect(windowAt(blocks, moveUp(blocks, pos, step), height).rows).toEqual(ref.slice(upRow, upRow + height));
									const down = moveDown(blocks, pos, step, height);
									if (row + step + height >= ref.length) expect(down).toBeUndefined();
									else expect(windowAt(blocks, down!, height).rows).toEqual(ref.slice(row + step, row + step + height));
								}
							}
						}
					}
				}
			}
		}
	});

	test("the next frame's resolve shows the same rows as the step that produced the position", () => {
		const list: Block[] = [
			{ id: 0, rows: ["a0", "a1"] },
			{ id: 1, rows: ["b0"] },
			{ id: 2, rows: ["c0", "c1", "c2"] },
			{ id: 3, rows: ["d0"] },
			{ id: 4, rows: ["e0", "e1"] },
		];
		const blocks = blocksOf(list);
		const height = 2;
		for (let back = 0; back < compose(list).length; back++) {
			const pos = positionFromTail(blocks, back, height)!;
			expect(windowAt(blocks, resolve(blocks, pos)!, height).rows).toEqual(windowAt(blocks, pos, height).rows);
			for (const step of [1, 2, 3]) {
				const down = moveDown(blocks, pos, step, height);
				if (down) expect(windowAt(blocks, resolve(blocks, down)!, height).rows).toEqual(windowAt(blocks, down, height).rows);
				const up = moveUp(blocks, pos, step);
				expect(windowAt(blocks, resolve(blocks, up)!, height).rows).toEqual(windowAt(blocks, up, height).rows);
			}
		}
	});

	test("a transcript ending in empty and all-blank blocks has no trailing separator", () => {
		const list: Block[] = [{ id: 0, rows: ["a0", "a1"] }, { id: 1, rows: ["b0", "b1", "b2"] }, { id: 2, rows: [] }, { id: 3, rows: ["  "] }];
		const blocks = blocksOf(list);
		const ref = compose(list);
		expect(windowAt(blocks, positionFromTail(blocks, 0, 3)!, 3)).toEqual({ rows: ref.slice(ref.length - 3), atEnd: true });
		expect(moveDown(blocks, positionFromTail(blocks, 1, 3)!, 1, 3)).toBeUndefined();
	});

	test("output appended at the tail does not move a scrolled-back view", () => {
		for (const [name, original] of CASES) {
			const list = original.map(b => ({ ...b }));
			const blocks = blocksOf(list);
			if (compose(list).length < 6) continue;
			const pos = positionFromTail(blocks, 2, 3)!;
			const before = windowAt(blocks, pos, 3).rows;
			list.push({ id: 900, rows: [] }, { id: 901, rows: ["tail1", "tail2"] }, { id: 902, rows: [" "] });
			expect(windowAt(blocks, resolve(blocks, pos)!, 3).rows, name).toEqual(before);
		}
	});

	test("a position survives blocks inserted before it and falls back when its block is removed", () => {
		const list = transcript(120, 7, 0.1);
		const blocks = blocksOf(list);
		const pos = positionFromTail(blocks, 60, 10)!;
		const before = windowAt(blocks, pos, 10).rows;
		list.unshift({ id: -1, rows: ["new"] }, { id: -2, rows: [] });
		const moved = resolve(blocks, pos)!;
		expect(moved.block).toBe(pos.block);
		expect(moved.index).toBe(pos.index + 2);
		expect(windowAt(blocks, moved, 10).rows).toEqual(before);
		list.splice(moved.index, 1);
		expect(resolve(blocks, moved)).toEqual({ block: list[moved.index]!, index: moved.index, offset: 0, source: list });
	});

	test("a block that shrinks under the view clamps the offset to its last row", () => {
		const list: Block[] = [{ id: 0, rows: ["a0", "a1"] }, { id: 1, rows: ["b0", "b1", "b2", "b3", "b4", "b5"] }, { id: 2, rows: ["c0"] }];
		const blocks = blocksOf(list);
		const pos: Position<Block> = { block: list[1]!, index: 1, offset: 5, source: list };
		list[1]!.rows = ["b0", "b1"];
		const shrunk = resolve(blocks, pos)!;
		expect(shrunk.offset).toBe(2); // two rows plus the separator before c0
		const row = rowOf(unitLengths(list), shrunk);
		expect(windowAt(blocks, shrunk, 2).rows).toEqual(compose(list).slice(row, row + 2));
	});

	test("PgUp and Ctrl+Home keep the live tail exactly when the whole transcript fits on screen", () => {
		for (const [name, list] of CASES) {
			const blocks = blocksOf(list);
			const total = compose(list).length;
			for (const height of [1, 3, 7, 40, 200]) {
				const live = total <= height;
				expect(scrolledBack(blocks, top(blocks), height) === undefined, `${name} h${height} Ctrl+Home`).toBe(live);
				for (const back of [1, 3, 29]) {
					const pos = scrolledBack(blocks, positionFromTail(blocks, back, height), height);
					expect(pos === undefined, `${name} h${height} PgUp ${back}`).toBe(live);
				}
			}
		}
	});

	test("rows below the view shrinking keep it scrolled back at the last rows, until the whole transcript fits", () => {
		const list: Block[] = [
			{ id: 0, rows: ["a0", "a1"] },
			{ id: 1, rows: ["b0"] },
			{ id: 2, rows: ["c0", "c1", "c2"] },
			{ id: 3, rows: ["d0"] },
			{ id: 4, rows: ["e0", "e1", "e2", "e3"] },
		];
		const blocks = blocksOf(list);
		const height = 3;
		const pos = positionFromTail(blocks, 2, height)!;
		list[4]!.rows = ["e0"];
		const settled = settle(blocks, pos, height);
		expect(settled).toBeDefined();
		const shown = windowAt(blocks, settled!, height).rows;
		expect(shown).toEqual(compose(list).slice(-height));
		list.push({ id: 5, rows: ["f0", "f1"] });
		expect(windowAt(blocks, settle(blocks, settled!, height)!, height).rows).toEqual(shown);

		const short: Block[] = [{ id: 0, rows: ["a0", "a1"] }, { id: 1, rows: ["b0"] }, { id: 2, rows: ["c0", "c1", "c2"] }];
		const shortBlocks = blocksOf(short);
		const above = positionFromTail(shortBlocks, 4, height)!;
		expect(windowAt(shortBlocks, above, height).rows).toEqual(["a1", "", "b0"]);
		short[1]!.rows = [];
		short[2]!.rows = [];
		expect(settle(shortBlocks, above, height)).toBeUndefined();
	});

	test("a block list replaced without the view's block sends the view to the live tail", () => {
		let list = transcript(40, 11, 0.1);
		const blocks: Blocks<Block> = {
			get length() {
				return list.length;
			},
			at: i => list[i]!,
			rows: b => trimBlankEdges(b.rows),
			get source() {
				return list;
			},
		};
		const pos = positionFromTail(blocks, 20, 5)!;
		const before = windowAt(blocks, pos, 5).rows;
		// Rebuilt from the same blocks, as a rebuild that reuses settled components does: the view stays.
		list = [...list];
		const kept = resolve(blocks, pos)!;
		expect(kept.block).toBe(pos.block);
		expect(windowAt(blocks, kept, 5).rows).toEqual(before);
		// The rebuilt list losing that block in place still falls back to its old index.
		list.splice(kept.index, 1);
		const fallback = resolve(blocks, kept)!;
		expect(fallback).toEqual({ block: list[kept.index]!, index: kept.index, offset: 0, source: list });
		// Rebuilt from new blocks: nothing to return to.
		list = transcript(60, 13, 0.1);
		expect(resolve(blocks, fallback)).toBeUndefined();
		expect(settle(blocks, fallback, 5)).toBeUndefined();
	});

	test("trimBlankEdges drops only all-blank edge rows", () => {
		expect(trimBlankEdges([" ", "a", "", "b", "\t"])).toEqual(["a", "", "b"]);
	});
});
