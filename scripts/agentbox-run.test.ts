import { describe, expect, test } from "bun:test";
import { relaunchArgv } from "./agentbox-run.ts";

const resumedSessionId = "NEW";

describe("relaunchArgv", () => {
	test.each([
		{
			name: "drops the original resume selector and launch prompt",
			argv: ["omp", "--resume", "OLD", "Your session was restarted in a new agentbox. ..."],
			expected: ["omp", "--resume", resumedSessionId, "."],
		},
		{
			name: "keeps configuration flags and their values",
			argv: ["omp", "--model", "x", "--append-system-prompt", "text"],
			expected: ["omp", "--model", "x", "--append-system-prompt", "text", "--resume", resumedSessionId, "."],
		},
		{
			name: "drops inline resume and continuation selectors",
			argv: ["omp", "--resume=OLD", "-c", "--continue"],
			expected: ["omp", "--resume", resumedSessionId, "."],
		},
		{
			name: "drops the end-of-options delimiter and literal prompt text",
			argv: ["omp", "--model", "x", "--", "fix the bug"],
			expected: ["omp", "--model", "x", "--resume", resumedSessionId, "."],
		},
		{
			name: "drops a bare positional prompt",
			argv: ["omp", "fix the bug"],
			expected: ["omp", "--resume", resumedSessionId, "."],
		},
		{
			name: "keeps an unknown extension flag and its value",
			argv: ["omp", "--ext-flag", "value"],
			expected: ["omp", "--ext-flag", "value", "--resume", resumedSessionId, "."],
		},
	])("$name", ({ argv, expected }) => {
		expect(relaunchArgv(argv, resumedSessionId)).toEqual(expected);
	});
});
