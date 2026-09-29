// Fixtures the viewport suites share: seeded transcripts, a block list over
// them, and omp's `TranscriptContainer.renderTail` composition written
// forwards, the reference the window and selection code are checked against.
import { type Blocks, type Position, trimBlankEdges } from "../viewport-window";

export type Block = { id: number; rows: string[] };

/** Park-Miller: every step stays exact (48271 x (2^31 - 1) < 2^53). */
export function random(seed: number): () => number {
	let state = seed;
	return () => (state = (state * 48271) % 2147483647) / 2147483647;
}

/** Blocks over `list`, read fresh on every call; `source` is the array, as viewport.ts passes `transcript.children`. */
export function blocksOf(list: Block[]): Blocks<Block> {
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
export function compose(list: Block[]): string[] {
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
export function unitLengths(list: Block[]): number[] {
	const lengths = list.map(b => trimBlankEdges(b.rows).length);
	return lengths.map((n, i) => (n === 0 ? 0 : n + (lengths.slice(i + 1).some(m => m > 0) ? 1 : 0)));
}

/** A position's absolute row in `compose(list)`, given `units = unitLengths(list)`. */
export function rowOf(units: readonly number[], pos: Position<Block>): number {
	return units.slice(0, pos.index).reduce((a, b) => a + b, 0) + pos.offset;
}

/**
 * Each row of `compose(list)`, written forwards: the index of the block it came
 * from and its offset into that block's unit. A separator belongs to the block
 * above it, at the offset one past that block's last row.
 */
export function originTable(list: Block[]): Array<{ index: number; offset: number }> {
	const origins: Array<{ index: number; offset: number }> = [];
	let above: { index: number; offset: number } | undefined;
	list.forEach((block, index) => {
		const rows = trimBlankEdges(block.rows).length;
		if (rows === 0) return;
		if (above) origins.push(above);
		for (let offset = 0; offset < rows; offset++) origins.push({ index, offset });
		above = { index, offset: rows };
	});
	return origins;
}

/**
 * Deterministic transcripts: blocks of 1-5 rows from `row(id, r)` with
 * interior blank rows, blank edges, all-blank blocks and empty blocks at
 * `emptyRate`.
 */
export function transcript(
	count: number,
	seed: number,
	emptyRate: number,
	row: (id: number, r: number) => string = (id, r) => `b${id}r${r}`,
): Block[] {
	const next = random(seed);
	return Array.from({ length: count }, (_, id) => {
		const kind = next();
		if (kind < emptyRate) return { id, rows: [] };
		if (kind < emptyRate + 0.05) return { id, rows: ["   ", "", "\t"] };
		const body = Array.from({ length: 1 + Math.floor(next() * 5) }, (_, r) => (next() < 0.1 ? "" : row(id, r)));
		body[0] ||= `b${id}first`;
		body[body.length - 1] ||= `b${id}last`;
		return { id, rows: kind < emptyRate + 0.2 ? ["", ...body, " "] : body };
	});
}
