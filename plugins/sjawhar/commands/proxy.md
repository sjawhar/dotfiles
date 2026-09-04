---
name: proxy
description: Shadow-run Sami's proxy on the question you just asked him. Consult the sami-proxy agent, show its verdict, then let Sami rule.
disable-model-invocation: true
---

# Proxy

I am running the proxy in shadow mode. Do not act on its verdict until I rule.

1. **Restate the question you just asked me**, verbatim, as the proxy's input. Include the context a stranger to this session needs to answer it: what I asked you to do, what you have done, what you are stuck on or deciding, and every option you offered me, with your recommendation. Do not soften or reshape the question to make it easier — the proxy must see exactly what I saw.
2. **Dispatch `task(agent="sami-proxy")`** with that input. Wait for it. Do nothing else in the meantime. The proxy always commits to an answer — it has no way to hand the question back to me. If its output contains no answer, or says the question must go to me without predicting what I would say, that is a defect: show me the output as-is and say so.
3. **Show me its verdict verbatim**, under a heading that is exactly:

   ```
   ## Proxy verdict — <TYPE>
   ```

   followed by the proxy's full output, unedited, including its `Sami's call:` line. Then stop and wait.
4. **Record my ruling.** My next message is the ruling. Echo it back under a heading that is exactly one of:

   ```
   ## Proxy ruling — AGREE
   ## Proxy ruling — DISAGREE
   ## Proxy ruling — PARTIAL
   ```

   AGREE: I accepted the proxy's answer; proceed on it. DISAGREE: I overruled it; my message is the answer; proceed on mine. PARTIAL: I accepted the direction but changed the substance; state in one line what changed, then proceed on my version. Directly under the heading, add one line `Sami's call: right|wrong` — whether the proxy was right about needing to ask me at all (wrong if it said yes and I said this was not a question for me, or said no and I wanted to be asked). If my message does not make the ruling obvious, ask me which of the three it is — that one question only.

The headings are markers for a later retro that measures how often the proxy and I agree. Never omit them, never paraphrase them, never emit them when this command was not invoked.
