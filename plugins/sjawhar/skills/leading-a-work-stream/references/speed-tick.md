# The speed tick: the 3-hourly loop and its reminder

## The loop, on each tick

1. Read the stream's state from its records: the numbers, today's plan against what merged, the hourly Dispatch check.
2. Ask each agent on Envoy what slows it down most right now. Not status.
3. Answer in writing: how can I help this go faster while the targets and the checks that gate them stay unchanged; how can I do better work faster; how can I help the team do better work faster?
4. Give the answers to a fresh subagent to attack — is each target ambitious, is each change concrete, is each date padded — and rewrite what it finds weak, because your own answers read better to you than they are.
5. Make the change now, or start an agent on it.
6. Append the tick to the stream's speed log (an artifact `speed-log.md` on the stream issue): the time, the change, the number it should move and by how much; on the next tick, what it did.

## Arming the reminder

Arm the reminder as an omp background process in your own session: a bash call with `async: true` and no deadline, whose output wakes the session when it ends. This one ends at the next of 01, 04, 07, 10, 13, 16, 19 and 22 UTC:

```bash
sleep $(( 10800 - ($(date +%s) - 3600) % 10800 )) && echo "SPEED TICK: run the six-step loop in leading-a-work-stream references/speed-tick.md now"
```

Re-arm it as the first step of each tick. It dies with the session, so put that command in your wake file as the first action after a restart. A hand-written systemd unit, timer or screen would outlive the session, but it lives in no repository, so a rebuilt box loses it.
