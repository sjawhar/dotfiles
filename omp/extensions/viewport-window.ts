// Scrolled-back windowing for the transcript viewport (viewport.ts).
//
// Rendered, the transcript is each block's rows with blank edges trimmed,
// empty blocks dropped, and one blank row between consecutive non-empty
// blocks: omp's `TranscriptContainer.renderTail` composition. Here each
// non-empty block owns a unit: its rows, plus that blank separator when a
// later block is non-empty. A position is a block and a row offset into its
// unit, so drawing a window or moving the position renders only the blocks it
// passes over, however long the transcript is.

export interface Blocks<B> {
	readonly length: number;
	at(index: number): B;
	/**
	 * The block's rows at the current width, blank edges trimmed; empty when it
	 * draws nothing. Every operation here asks for the same block's rows several
	 * times, so an adapter that renders must memoize them for the operation.
	 */
	rows(block: B): readonly string[];
	/** The list the blocks are read from: a different value means it was replaced wholesale. */
	readonly source: unknown;
}

/** The top row of a scrolled-back view. */
export interface Position<B> {
	block: B;
	/** Where `block` sat when this was made; `resolve` re-finds it if the list moved. */
	index: number;
	/** Row offset into the block's unit. */
	offset: number;
	/** The `Blocks.source` this was made from. */
	source: unknown;
}

/** Strips leading and trailing all-blank rows, as omp's `trimBlankEdges` does. */
export function trimBlankEdges(rows: readonly string[]): readonly string[] {
	let start = 0;
	let end = rows.length;
	while (start < end && !/\S/.test(rows[start]!)) start++;
	while (end > start && !/\S/.test(rows[end - 1]!)) end--;
	return start === 0 && end === rows.length ? rows : rows.slice(start, end);
}

function nextNonEmpty<B>(blocks: Blocks<B>, from: number): number {
	for (let index = Math.max(0, from); index < blocks.length; index++) {
		if (blocks.rows(blocks.at(index)).length > 0) return index;
	}
	return -1;
}

function previousNonEmpty<B>(blocks: Blocks<B>, from: number): number {
	for (let index = Math.min(from, blocks.length - 1); index >= 0; index--) {
		if (blocks.rows(blocks.at(index)).length > 0) return index;
	}
	return -1;
}

/** Rows in block `index`'s unit: its rows plus a separator when a later block draws something. */
function unitLength<B>(blocks: Blocks<B>, index: number): number {
	const rows = blocks.rows(blocks.at(index)).length;
	return rows === 0 ? 0 : rows + (nextNonEmpty(blocks, index + 1) >= 0 ? 1 : 0);
}

function indexOf<B>(blocks: Blocks<B>, block: B): number {
	for (let index = 0; index < blocks.length; index++) if (blocks.at(index) === block) return index;
	return -1;
}

function positionAt<B>(blocks: Blocks<B>, index: number, offset: number): Position<B> {
	return { block: blocks.at(index), index, offset, source: blocks.source };
}

/** The first row of the transcript; undefined when no block draws anything. */
export function top<B>(blocks: Blocks<B>): Position<B> | undefined {
	const index = nextNonEmpty(blocks, 0);
	return index < 0 ? undefined : positionAt(blocks, index, 0);
}

export function isTop<B>(blocks: Blocks<B>, pos: Position<B>): boolean {
	return pos.offset === 0 && previousNonEmpty(blocks, pos.index - 1) < 0;
}

/**
 * Re-finds `pos.block` after the block list changed. A block removed from the
 * list falls back to whatever now sits at its old index, at offset 0. A block
 * missing from a list that was replaced wholesale has nothing to fall back to:
 * undefined, as for an empty list.
 */
export function resolve<B>(blocks: Blocks<B>, pos: Position<B>): Position<B> | undefined {
	if (blocks.length === 0) return undefined;
	const index =
		pos.index < blocks.length && blocks.at(pos.index) === pos.block ? pos.index : indexOf(blocks, pos.block);
	if (index >= 0) return positionAt(blocks, index, Math.min(pos.offset, Math.max(0, unitLength(blocks, index) - 1)));
	return pos.source === blocks.source ? positionAt(blocks, Math.min(pos.index, blocks.length - 1), 0) : undefined;
}

/** Up to `count` rows from `pos` down. */
function take<B>(blocks: Blocks<B>, pos: Position<B>, count: number): string[] {
	const out: string[] = [];
	let offset = pos.offset;
	for (let index = pos.index; index < blocks.length && out.length < count; index++) {
		const rows = blocks.rows(blocks.at(index));
		const length = unitLength(blocks, index);
		for (let row = offset; row < length && out.length < count; row++) out.push(row < rows.length ? rows[row]! : "");
		offset = 0;
	}
	return out;
}

/** Up to `height` rows from `pos` down; `atEnd` when they reach the transcript's last row. */
export function windowAt<B>(blocks: Blocks<B>, pos: Position<B>, height: number): { rows: string[]; atEnd: boolean } {
	const rows = take(blocks, pos, height + 1);
	return { rows: rows.slice(0, height), atEnd: rows.length <= height };
}

/** `pos`, or undefined (the live tail) when a view `height` rows tall from it reaches the last row. */
export function scrolledBack<B>(blocks: Blocks<B>, pos: Position<B> | undefined, height: number): Position<B> | undefined {
	return pos === undefined || windowAt(blocks, pos, height).atEnd ? undefined : pos;
}

/**
 * Where the next frame draws scrolled-back `pos`: re-found by `resolve`, and
 * moved up to show the last `height` rows once the rows below it shrank far
 * enough that its window reaches the end, so it stays scrolled back. Undefined
 * (the live tail) when `resolve` has nothing to return to, no block draws
 * anything, or the whole transcript now fits on screen: a tail view that starts
 * at the first row has nothing to scroll back to.
 */
export function settle<B>(blocks: Blocks<B>, pos: Position<B>, height: number): Position<B> | undefined {
	const current = resolve(blocks, pos);
	if (current === undefined) return undefined;
	const kept = scrolledBack(blocks, current, height);
	if (kept !== undefined) return kept;
	const tail = positionFromTail(blocks, 0, height);
	return tail === undefined || isTop(blocks, tail) ? undefined : tail;
}

/**
 * The top of a view `height` rows tall whose last row sits `back` rows above the
 * transcript's last row, clamped at the first row.
 */
export function positionFromTail<B>(blocks: Blocks<B>, back: number, height: number): Position<B> | undefined {
	let need = back + height;
	for (let index = previousNonEmpty(blocks, blocks.length - 1); index >= 0; index = previousNonEmpty(blocks, index - 1)) {
		const length = unitLength(blocks, index);
		if (need <= length) return positionAt(blocks, index, length - need);
		need -= length;
	}
	return top(blocks);
}

/** `pos` moved up `rows` rows, clamped at the first row. */
export function moveUp<B>(blocks: Blocks<B>, pos: Position<B>, rows: number): Position<B> {
	let index = pos.index;
	let offset = pos.offset - rows;
	while (offset < 0) {
		const previous = previousNonEmpty(blocks, index - 1);
		if (previous < 0) {
			offset = 0;
			break;
		}
		index = previous;
		offset += unitLength(blocks, index);
	}
	return positionAt(blocks, index, offset);
}

/** `pos` moved down `rows` rows; undefined once a view `height` rows tall from there reaches the last row. */
export function moveDown<B>(blocks: Blocks<B>, pos: Position<B>, rows: number, height: number): Position<B> | undefined {
	let index = pos.index;
	let offset = pos.offset + rows;
	while (index < blocks.length) {
		const length = unitLength(blocks, index);
		if (offset < length) break;
		offset -= length;
		index++;
	}
	return index >= blocks.length ? undefined : scrolledBack(blocks, positionAt(blocks, index, offset), height);
}
