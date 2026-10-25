# Coder brief

The lead pastes this file whole into the coder's task, with the path of `findings.jsonl` (`count-types.py export`), the checker's `verdict.json`, and last run's type names (from its `counts.json`, if there is one). The coder is a fresh agent that reads only these files.

1. Drop the ids `verdict.json` rejected.
2. Read every finding's `code`, `what_happened` and quotes before you name anything. Do not start from a list of categories: a list you bring decides the answer.
3. Group findings that share a cause into failure types. Name each type as the agent's action plus what it skipped ("waiting on Sami without checking that the wait is still real"), with a one-sentence definition and a test that decides membership. Split a group whose findings would need different fixes; merge groups that one fix would prevent.
4. Reuse a type name from last run when it is the same failure, so this run's count compares with last run's. Write a new name only for a new failure.
5. Ids ending in `~<n>` are `sami_patterns` entries (`count-types.py export` writes them). Group those the same way, into pattern types of their own.
6. Write `types.jsonl` (one line per id: `{"id": "M0012#0", "type": "<name>"}`; every remaining id exactly once) and `types.md` (each type: name, definition, membership test, two example ids).

Your final message: the number of types, and each type with its finding count.
