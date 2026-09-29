import { describe, expect, test } from "bun:test";
import {
	type Blocks,
	comparePositions,
	isTop,
	moveDown,
	moveUp,
	type Position,
	positionFromTail,
	resolve,
	rowsBetween,
	scrolledBack,
	settle,
	top,
	trimBlankEdges,
	windowAt,
	windowWithPositions,
} from "../viewport-window";
import { type Block, blocksOf, compose, originTable, random, rowOf, transcript, unitLengths } from "./transcript-fixtures";

/** Seeded property tests: bun's default 5 s per test is too short for them on a loaded box. */
const PROPERTY_TIMEOUT_MS = 30_000;

/** Index and offset of each position, for comparison with `originTable`. */
const origins = (positions: readonly Position<Block>[]) => positions.map(({ index, offset }) => ({ index, offset }));

const CASES: Array<[string, Block[]]> = [];
// Three seeds keep every test here fast while still killing every
// behavior-changing mutant tried against the module.
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
	}, PROPERTY_TIMEOUT_MS);

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
	}, PROPERTY_TIMEOUT_MS);

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

	test("windowWithPositions reports each drawn row's position, blank rows and separators included", () => {
		for (let seed = 1; seed < 20; seed++) {
			for (const count of [1, 2, 4, 9, 20]) {
				for (const emptyRate of [0, 0.4, 0.8]) {
					const list = transcript(count, seed * 104729, emptyRate);
					const ref = compose(list);
					const table = originTable(list);
					expect(table.length).toBe(ref.length);
					const units = unitLengths(list);
					const blocks = blocksOf(list);
					for (let index = 0; index < list.length; index++) {
						for (let offset = 0; offset < Math.max(1, units[index]!); offset++) {
							const pos: Position<Block> = { block: list[index]!, index, offset, source: list };
							const row = rowOf(units, pos);
							for (const height of [1, 3, 6]) {
								const view = windowWithPositions(blocks, pos, height);
								expect(view.rows).toEqual(ref.slice(row, row + height));
								expect(origins(view.positions)).toEqual(table.slice(row, row + height));
								expect(view.positions.every(p => p.block === list[p.index] && p.source === list)).toBe(true);
								expect(view.atEnd).toBe(row + height >= ref.length);
								expect(windowAt(blocks, pos, height)).toEqual({ rows: view.rows, atEnd: view.atEnd });
							}
						}
					}
				}
			}
		}
	}, PROPERTY_TIMEOUT_MS);

	test("rowsBetween is the composed rows from one drawn position through another, and comparePositions orders them", () => {
		const next = random(4099);
		for (const [name, list] of CASES) {
			const ref = compose(list);
			const blocks = blocksOf(list);
			if (ref.length === 0) continue;
			const drawn = windowWithPositions(blocks, top(blocks)!, ref.length).positions;
			expect(origins(drawn), name).toEqual(originTable(list));
			for (let pair = 0; pair < 40; pair++) {
				const i = Math.floor(next() * ref.length);
				const j = pair === 0 ? i : Math.floor(next() * ref.length);
				const a = drawn[i]!;
				const b = drawn[j]!;
				expect(Math.sign(comparePositions(blocks, a, b)), `${name} ${i} vs ${j}`).toBe(Math.sign(i - j));
				const [low, high, from, to] = i <= j ? [i, j, a, b] : [j, i, b, a];
				expect(rowsBetween(blocks, from, to), `${name} ${low}..${high}`).toEqual({
					rows: ref.slice(low, high + 1),
					positions: drawn.slice(low, high + 1),
				});
				if (low < high) expect(() => rowsBetween(blocks, to, from), `${name} ${high}..${low}`).toThrow();
			}
			// A position whose block is gone from a replaced list no longer resolves.
			const gone: Position<Block> = { block: { id: -1, rows: ["gone"] }, index: 0, offset: 0, source: [] };
			expect(() => comparePositions(blocks, drawn[0]!, gone), name).toThrow();
		}
	}, PROPERTY_TIMEOUT_MS);

	test("trimBlankEdges drops only all-blank edge rows", () => {
		expect(trimBlankEdges([" ", "a", "", "b", "\t"])).toEqual(["a", "", "b"]);
	});
});
