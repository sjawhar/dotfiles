# Questions for Sami go through the lead first

Sami set this up in a note to the chief of staff on 2026-10-26 at 05:02Z. In the week before, he answered 40 to 122 asks a day (122 on 10-25) from about 70 agents, and each one cost him a read and a decision.

## The steps

1. **The agent sends you its draft.** An agent on your team that would open a Dispatch ask for Sami first sends you the ask as it would open it, on Envoy: the question, the options, its recommendation, and the facts it measured, with their links. It does not open the ask yet. It keeps working on everything the answer does not block.
2. **You check what Sami already said.** Search his answers and messages: `dispatch search --query '<two or three nouns>'`, `tq search --human "<phrase>"`, and your stream's goals document. For a second read, consult the `sami-proxy` agent with the draft and what you found.
3. **You answer, or the ask goes to him.**
   - When one of his answers covers this case, and the facts that decided it are the same here, answer the agent and link that answer. The agent records the decision with your answer and the link, as `skill://sami-proxy` says under "A recorded decision".
   - When nothing he said covers it, or you are not sure it does, tell the agent to open the ask, as written or with your fixes to its format. The agent opens it. Then you run `dispatch follow --ask <id>`, so his answer reaches you as well as the agent.
   - Answer within the hour. A question that waits on you costs the agent as much as one that waits on him.
4. **You keep his answer.** Add each answer of his that reaches you to the stream's record of his words ([user-quotes.md](user-quotes.md)), with the question it answered and the facts that decided it, so the next similar question gets the same answer from you.

Your own questions for Sami go the same way to the chief of staff (session 01a13dca-e979-7264-af32-f654ef57a6f5).

## Apply his answers with discernment

Sami's warning when he set this up: a lead turns one thing he told one agent once into a blanket rule, and agents then believe something is forbidden. That deranges the work.

- An answer settles the case it answered. Before you use it for another case, check that the facts that decided it hold in the new case. If one differs, the question goes to him.
- An answer to one agent's question is not a rule for your team. Send it only to the agent whose question it answers. When Sami states a rule for everyone, he says so: in his CLAUDE.md, or in a message to all agents or all leads.
- Read his answers on one topic together, and state the requirement or constraint behind them in your own words, marked as your reading. When two answers disagree, find what differed between the two cases before you choose one; the newer answer is not automatically the rule.
- When an agent cites one of his answers as a ban, read the answer's own words and context, and tell the agent if it reads more into it than he said.
