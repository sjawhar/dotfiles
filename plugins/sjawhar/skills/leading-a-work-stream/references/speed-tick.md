# The speed tick: the 3-hourly loop and a reminder that survives restarts

## The loop, on each tick

1. Read the stream's state from its records: the numbers, today's plan against what merged, the hourly Dispatch check.
2. Ask each agent on Envoy what slows it down most right now. Not status.
3. Answer in writing: how can I help this go faster while the targets and the checks that gate them stay unchanged; how can I do better work faster; how can I help the team do better work faster?
4. Give the answers to a fresh subagent to attack — is each target ambitious, is each change concrete, is each date padded — and rewrite what it finds weak, because your own answers read better to you than they are.
5. Make the change now, or start an agent on it.
6. Append the tick to the stream's speed log (an artifact `speed-log.md` on the stream issue): the time, the change, the number it should move and by how much; on the next tick, what it did.

## Arming the reminder

A reminder inside the session (`sleep` in a background job) dies with the session's processes. A systemd user timer survives them and wakes the session with an Envoy message. Arm it once, from a shell on the machine the session runs on.

Replace `NAME` with a short name for your stream and `SESSION` with your full session id (`envoy ps` lists it). The timer below fires at 01, 04, 07, 10, 13, 16, 19 and 22 UTC; change `OnCalendar` for another rhythm.

```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/speed-tick-NAME.service <<'EOF'
[Unit]
Description=Speed tick for NAME's lead

[Service]
Type=oneshot
ExecStart=%h/.dotfiles/scripts/envoy send SESSION "SPEED TICK: run the six-step loop in leading-a-work-stream references/speed-tick.md now."
EOF
cat > ~/.config/systemd/user/speed-tick-NAME.timer <<'EOF'
[Unit]
Description=Speed tick for NAME's lead, every 3 hours

[Timer]
OnCalendar=*-*-* 01/3:00:00
Persistent=true

[Install]
WantedBy=timers.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now speed-tick-NAME.timer
systemctl --user list-timers speed-tick-NAME.timer
```

A session that runs where it cannot install a user timer (a pod) arms an equivalent wake-up there, and says in its speed log how.
