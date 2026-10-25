# Optional: Tailscale SSH (keyless SSH from mobile)

Run `sudo tailscale up --ssh`, then add this SSH ACL in the Tailscale admin console:

```json
"ssh": [{"action": "check", "src": ["autogroup:member"], "dst": ["autogroup:self"], "users": ["autogroup:nonroot"]}]
```

Tailscale SSH bypasses `sshd`, so the `sshd` hardening and fail2ban of Step 5 do not apply to it. Only Tailscale ACLs control it.
