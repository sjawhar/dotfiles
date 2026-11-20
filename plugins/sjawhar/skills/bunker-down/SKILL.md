---
name: bunker-down
description: Use when hardening a publicly reachable EC2 instance used as a daily development machine. Also use when adding Tailscale, a host firewall, SSH hardening or SSM access to one.
---

# Harden an EC2 devbox

Apply each setting below where the box's current config is looser. Where the box already has a stricter value (a lower `MaxAuthTries`, a longer `bantime`), keep that value. Tell the user each setting you change.

- Open each new access path and confirm it works before you close the old one, so the user is never locked out.
- At each GATE, stop until the user confirms.
- Needs: an authenticated AWS CLI (on the box or the user's laptop), the user's Tailscale account, and working SSH now.

## 1. Assess

Report each item as good or concerning: OS release and patch state, listening sockets, firewall (ufw, iptables, nftables), `sshd -T` and every file in `/etc/ssh/sshd_config.d/`, login-capable accounts, `/root/.ssh/authorized_keys`, fail2ban, unattended-upgrades, AppArmor, auditd, network sysctls, enabled services, published Docker ports, and from AWS the security groups, IMDS options and IAM instance profile. An item is concerning when it allows more than Steps 2 to 7 set (for example password SSH login, IMDSv1, an inbound rule other than UDP 41641), when the internet can reach it (a published Docker port, a socket on a public address that a security group lets in), when it is off or behind (AppArmor or auditd not running, a package that `apt list --upgradable` lists), or when it is an account other than the user's and root with a login shell. Otherwise it is good.

Record for later steps: `INSTANCE_ID` (instance metadata or `aws ec2 describe-instances`), `REGION` (instance metadata), `ACCOUNT_ID` (`aws sts get-caller-identity`), `VPC_ID` (`aws ec2 describe-instances`), `OLD_SG_ID` (the instance's current security group), and after Step 3 `TAILSCALE_IP` (`tailscale ip -4`).

## 2. SSM escape hatch

1. Do this first: SSM access does not depend on SSH, Tailscale or network rules. Check that the agent runs: `sudo systemctl status snap.amazon-ssm-agent.amazon-ssm-agent.service` (`amazon-ssm-agent` when it is not a snap). If it is missing: `sudo snap install amazon-ssm-agent --classic`.
2. Read `references/ssm-escape-hatch.md` now: it has the two IAM policies and the commands that create and attach the role and install the Session Manager plugin.
3. GATE: the user runs `aws ssm start-session --target $INSTANCE_ID --region $REGION` from their laptop.

## 3. Tailscale

Install from the apt repository, whose packages are signature-checked, not with `curl ... | sh`:

```bash
curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/$(lsb_release -cs).noarmor.gpg \
  | sudo tee /usr/share/keyrings/tailscale-archive-keyring.gpg >/dev/null
curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/$(lsb_release -cs).tailscale-keyring.list \
  | sudo tee /etc/apt/sources.list.d/tailscale.list
sudo apt-get update && sudo apt-get install -y tailscale
sudo tailscale up
```

The user opens the auth URL; then run `tailscale ip -4`. GATE: the user SSHes to the Tailscale IP from their laptop or phone.

## 4. UFW

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow in on tailscale0          # all traffic over Tailscale, SSH included
sudo ufw allow 41641/udp                 # WireGuard direct connections
sudo ufw --force enable
sudo ufw status verbose
```

GATE: the user can still SSH to the Tailscale IP.

## 5. SSH and fail2ban

Ask the user whether they use SFTP (VS Code Remote, scp, rsync over SSH). Name it `00-hardening.conf`: sshd keeps the first value it reads, so a file sorted before it would win. Where Step 1 found a stricter value, write that one.

```bash
SFTP_LINE="Subsystem sftp /bin/false"         # no SFTP
# SFTP_LINE="Subsystem sftp internal-sftp"    # the user needs SFTP
sudo tee /etc/ssh/sshd_config.d/00-hardening.conf << EOF
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
AuthenticationMethods publickey
X11Forwarding no
AllowAgentForwarding no
PermitTunnel no
MaxAuthTries 3
MaxSessions 5
LoginGraceTime 30
ClientAliveInterval 300
ClientAliveCountMax 2
$SFTP_LINE
EOF
sudo sshd -t && sudo systemctl reload ssh
```

GATE: keep your current session open; the user opens a new SSH session to the Tailscale IP. A reload that breaks authentication locks out everyone without an open session. Then:

```bash
sudo rm -f /root/.ssh/authorized_keys   # blocks root even if PermitRootLogin is ever undone
sudo apt-get install -y fail2ban
printf '[sshd]\nenabled = true\nport = ssh\nmaxretry = 5\nbantime = 600\nfindtime = 600\n' \
  | sudo tee /etc/fail2ban/jail.local
sudo systemctl enable --now fail2ban
```

Keyless SSH from a phone, only if the user wants it: run `sudo tailscale up --ssh`, then add `"ssh": [{"action": "check", "src": ["autogroup:member"], "dst": ["autogroup:self"], "users": ["autogroup:nonroot"]}]` to the ACL in the Tailscale admin console. Tailscale SSH bypasses `sshd`, so the settings above and fail2ban do not cover it; only the ACL does.

## 6. Security group

```bash
VPC_ID=$(aws ec2 describe-instances --instance-ids $INSTANCE_ID \
  --query 'Reservations[0].Instances[0].VpcId' --output text)
SG_ID=$(aws ec2 create-security-group --group-name devbox \
  --description "Devbox: Tailscale WireGuard only, no public SSH" \
  --vpc-id $VPC_ID --query 'GroupId' --output text)
aws ec2 authorize-security-group-ingress --group-id $SG_ID \
  --protocol udp --port 41641 --cidr 0.0.0.0/0
ENI=$(aws ec2 describe-instances --instance-ids $INSTANCE_ID \
  --query 'Reservations[0].Instances[0].NetworkInterfaces[0].NetworkInterfaceId' --output text)
aws ec2 modify-network-interface-attribute --network-interface-id $ENI --groups $SG_ID
```

GATE: SSH over Tailscale still works. Then `aws ec2 delete-security-group --group-id $OLD_SG_ID`.

## 7. OS hardening

Read `references/os-hardening.md` now and run it: it writes the network sysctls, sets IMDSv2 only, disables unused services, installs unattended-upgrades, and has the IPv6 question and the bashrc lines.

## 8. Security review

Give the `cybersecurity-expert` agent (the `task` tool) this brief, or do the review yourself if it is not available, and fix every HIGH finding before Step 9:

> Review the security posture of this EC2 instance, a personal development machine reached over Tailscale with SSM as an escape hatch: open ports, listening services, SSH config, firewall rules, IAM role scope, IMDS options, kernel params, permissions on ~/.ssh, /etc/shadow and sudoers, AppArmor, and the Docker daemon config if installed. Report anything else concerning. Label each finding HIGH, MEDIUM or LOW, HIGH first.

## 9. Final verification

Check each change end to end: read the live state with the command named below, not the file you wrote, and have the user confirm from their laptop that a new SSH session to the Tailscale IP works, that `ssh` to the public IP times out, and that `aws ssm start-session` works. Give the user a summary table: UFW status (`sudo ufw status verbose`), the effective `sudo sshd -T` values, the fail2ban sshd jail (`sudo fail2ban-client status sshd`), `tailscale status`, kernel params (`sysctl <key>` for each key in `/etc/sysctl.d/99-hardening.conf`), IMDS options (`aws ec2 describe-instances --instance-ids $INSTANCE_ID --query 'Reservations[0].Instances[0].MetadataOptions'`), listening ports (`sudo ss -tulpn`), and the new security group's ingress rules (`aws ec2 describe-security-groups --group-ids $SG_ID`).

## Common issues

- SSM agent does not register: check the instance has the IAM role, restart the agent, wait 30 s.
- Tailscale auth link expired: `sudo tailscale up` again.
- Locked out after `ufw enable`: connect over SSM and run `sudo ufw disable`.
- fail2ban banned you: `sudo fail2ban-client set sshd unbanip <YOUR_IP>`.
- SFTP clients break: rerun Step 5 with `SFTP_LINE="Subsystem sftp internal-sftp"`.
