# Common issues

| Situation | Action |
| --- | --- |
| SSM agent does not register | Check the instance has the IAM role, restart the agent, wait 30 s |
| Tailscale auth link expired | `sudo tailscale up` again |
| Locked out after `ufw enable` | Connect over SSM and run `sudo ufw disable` |
| fail2ban banned you | `sudo fail2ban-client set sshd unbanip <YOUR_IP>` |
| SFTP clients break | Rerun Step 5 with `SFTP_LINE="Subsystem sftp internal-sftp"` |
