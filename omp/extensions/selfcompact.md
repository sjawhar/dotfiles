You are about to decide whether to compress your conversation history into a summary that REPLACES the full history above. After compression, you continue the task from only [system prompt, the compaction summary, the most recent messages]. Compression is irreversible: anything not preserved in the summary is gone.

Compression is safe ONLY when ALL FOUR of the following hold:

- (C1) the trajectory has reached a closed unit (not mid-thought),
- (C2) the essential information is reducible to 3–5 cite-able facts without loss,
- (C3) something has progressed since the last compression,
- (N1) you are NOT currently stuck in a way summarization would mask.

Answer C1, C2, C3, N1 honestly. Each Y answer requires verbatim evidence quoted from the trajectory above; answers without evidence default to N.

C1 CLOSED-UNIT: The most recent assistant message is a closed unit — a completed tool call whose result is now visible, or a completed sub-analysis with a clear stopping point. It is NOT mid-sentence reasoning ("Let me now check…", "I should next look at…"), and not a half-formulated command. If Y, quote the closing fragment of the last assistant message. If N, quote the open fragment that shows the trajectory is mid-thought.

C2 SUMMARIZABLE: You can write 3–5 essential facts (with verbatim citations from the trajectory) that future-you needs to continue the task after compression. Each fact must be a single concrete statement: a file path, symbol, command that ran and its outcome, a test result, or a resolved sub-question. Answer N if the trajectory's value is dispersed across many small inferences (e.g., a list of approaches already ruled out and the error each produced, negative results that constrain hypothesis space) that would be lost without the dispersal. If Y, list the 3–5 facts numbered, each with a verbatim citation in quotes, separated by "|". If N, name in one sentence the class of information that would be lost.

C3 PROGRESS: Since the most recent compression (or since the start of the conversation if none), you have either obtained a new concrete fact (a file:line, a command's output, a test that passed or failed) OR refined the sub-question being pursued. If Y, name the new fact or refined sub-question. If N, state that you are returning the same state you compressed from.

N1 STUCK: At least 3 of your last 4 tool calls returned no new file, symbol, or command output you had not already seen (i.e., were duplicates or returned already-known content). If you have made fewer than 4 tool calls total, answer N. If Y, name 1 distinct strategy you have NOT yet tried (different tool, different command, different angle on the task). If N, name one new file, symbol, or command output obtained recently.

Output: exactly 4 lines, no preamble or trailing text.
C1: Y/N--<evidence>
C2: Y/N--<if Y: 1. fact "citation" | 2. fact "citation" | 3. fact "citation"; if N: <class of info lost>>
C3: Y/N--<evidence>
N1: Y/N--<evidence>
