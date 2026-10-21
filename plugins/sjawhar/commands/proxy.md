---
name: proxy
description: Shadow-run Sami's proxy on the question you just asked him. Consult the sami-proxy agent, show its verdict, then let Sami rule.
disable-model-invocation: true
---

# Proxy

Shadow mode: do not act on the proxy's verdict until Sami rules.

1. **Write the proxy's input.** Quote the question you just asked Sami, verbatim. Add what a stranger to this session needs: Sami's request, what you did, and what you are stuck on or deciding. Add every option you gave him, with your recommendation. Do not soften or reshape the question. The proxy must see what Sami saw.
2. **Run `task(agent="sami-proxy")`** with that input. Wait for it and do nothing else. The proxy must always commit to an answer. If its output has no answer, or sends the question to Sami with no prediction of his answer, that is a defect: show the output as-is and say so.
3. **Show the verdict.** Write this exact heading, then the proxy's full output unedited, with its `Sami's call:` line. Then stop and wait.

   ```
   ## Proxy verdict — <TYPE>
   ```

4. **Record the ruling.** Sami's next message is the ruling. Echo it under exactly one of these headings:

   ```
   ## Proxy ruling — AGREE
   ## Proxy ruling — DISAGREE
   ## Proxy ruling — PARTIAL
   ```

   | Ruling | Meaning | Then |
   |---|---|---|
   | AGREE | Sami accepted the proxy's answer | Proceed on it. |
   | DISAGREE | Sami overruled it. His message is the answer. | Proceed on his answer. |
   | PARTIAL | Sami kept the direction, changed the substance | State in one line what changed, then proceed on his version. |

   - Directly under the heading, add one line: `Sami's call: right|wrong`. It says if the proxy was right that the question needed Sami at all. It is `wrong` if the proxy said yes (ask Sami) and Sami said it was not his question. It is also `wrong` if the proxy said no and Sami wanted to be asked.
   - If his message does not make the ruling clear, ask which of the three it is. Ask only that one question.

A later retro counts these headings to measure how often the proxy and Sami agree. Never omit or paraphrase them. Never write them when this command was not invoked.
