# The speed tick: a reminder that survives restarts

A reminder inside the session (`sleep` in a background job) dies with the session's processes. A systemd user timer survives them and wakes the session with an Envoy message. Arm it once, from a shell on the machine the session runs on.

Replace `NAME` with a short name for your stream and `SESSION` with your full session id (`envoy ps` lists it). The timer below fires at 01, 04, 07, 10, 13, 16, 19 and 22 UTC; change `OnCalendar` for another rhythm.

```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/speed-tick-NAME.service <<'EOF'
[Unit]
Description=Speed tick for NAME's lead

[Service]
Type=oneshot
ExecStart=%h/.dotfiles/scripts/envoy send SESSION "SPEED TICK: run the speed loop in leading-a-work-stream (section The speed loop) now."
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
