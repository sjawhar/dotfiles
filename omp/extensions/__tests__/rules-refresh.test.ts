import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import * as fs from "node:fs/promises";
import * as os from "node:os";
import * as path from "node:path";
import { cachedReader, createRefresher } from "../experiments/rules-refresh";

const RULE_A = "Rule A: answer in English.";
const RULE_OLD = "Rule Old: always cite the spec section you relied on when you answer.";
const RULE_B = "Rule B: end every reply with the word BANANA.";

let dir: string;
let file: string;
beforeEach(async () => {
	dir = await fs.mkdtemp(path.join(os.tmpdir(), "rules-refresh-"));
	file = path.join(dir, "AGENTS.md");
});
afterEach(() => fs.rm(dir, { recursive: true, force: true }));

const systemPrompt = (content: string) => ["You are omp.", `<project-context>\n<file path="${file}">\n${content}\n</file>\n</project-context>`];

describe("rules refresh", () => {
	test("a file changed on disk yields its added paragraph verbatim and names the removed one", async () => {
		await Bun.write(file, `${RULE_A}\n\n${RULE_B}\n`);
		const notice = await createRefresher(cachedReader()).check(systemPrompt(`${RULE_A}\n\n${RULE_OLD}`));
		expect(notice).toContain(file);
		expect(notice).toContain(RULE_B);
		expect(notice).toContain(RULE_OLD.slice(0, 40));
		expect(notice).not.toContain(RULE_A);
	});

	test("a file matching its block yields nothing", async () => {
		await Bun.write(file, `${RULE_A}\n`);
		expect(await createRefresher(cachedReader()).check(systemPrompt(RULE_A))).toBeUndefined();
	});

	test("a change is delivered once, a further change again, and a session reset re-delivers", async () => {
		const refresher = createRefresher(cachedReader());
		const prompt = systemPrompt(RULE_A);
		await Bun.write(file, `${RULE_A}\n\n${RULE_B}`);
		expect(await refresher.check(prompt)).toContain(RULE_B);
		expect(await refresher.check(prompt)).toBeUndefined();

		const ruleC = "Rule C: never use emoji.";
		await Bun.write(file, `${RULE_A}\n\n${RULE_B}\n\n${ruleC}`);
		const again = await refresher.check(prompt);
		expect(again).toContain(ruleC);

		refresher.reset();
		expect(await refresher.check(prompt)).toContain(ruleC);
	});

	test("a context file that no longer exists is ignored", async () => {
		expect(await createRefresher(cachedReader()).check(systemPrompt(RULE_A))).toBeUndefined();
	});
});
