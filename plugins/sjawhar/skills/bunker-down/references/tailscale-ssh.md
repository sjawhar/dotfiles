# Step 5: keyless SSH from a phone

Only if the user wants it:

1. Run `sudo tailscale up --ssh`.
2. Add `"ssh": [{"action": "check", "src": ["autogroup:member"], "dst": ["autogroup:self"], "users": ["autogroup:nonroot"]}]` to the ACL in the Tailscale admin console.

Tailscale SSH bypasses `sshd`, so the Step 5 settings and fail2ban do not cover it; only the ACL does.
