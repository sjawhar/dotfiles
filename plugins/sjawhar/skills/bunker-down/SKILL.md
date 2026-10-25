---
name: bunker-down
description: Use when hardening a publicly reachable EC2 instance used as a daily development machine. Also use when adding Tailscale, a host firewall, SSH hardening or SSM access to one.
---

# Harden an EC2 devbox

If this skill disagrees with the code, a command's output or its `--help`, follow those and say which line of this skill is wrong.

- Never lock the user out. Open each new access path and confirm that it works before you close the old one.
- At each **GATE**, stop until the user confirms.
- Prerequisites: an authenticated AWS CLI (on the instance or the user's laptop), a Tailscale account for the user, and working SSH access now.

## Step 1: Assess

Read the current posture and present each finding as "good" or "concerning": OS release and patch state, listening sockets, firewall rules (ufw, iptables, nftables), the effective `sshd -T` settings and every drop-in under `/etc/ssh/sshd_config.d/`, login-capable accounts, `/root/.ssh/authorized_keys`, fail2ban, unattended-upgrades, AppArmor, auditd, network sysctls, enabled services, published Docker ports, and from AWS the instance's security groups, IMDS options and IAM instance profile.

Collect these values; later steps use them:

| Variable | Source |
|----------|--------|
| `$INSTANCE_ID` | Instance metadata or `aws ec2 describe-instances` |
| `$REGION` | Instance metadata |
| `$ACCOUNT_ID` | `aws sts get-caller-identity` |
| `$VPC_ID` | `aws ec2 describe-instances` |
| `$OLD_SG_ID` | Current security group on the instance |
| `$TAILSCALE_IP` | `tailscale ip -4` after Step 3 |

## Step 2: SSM escape hatch

Do this first: SSM gives access that does not depend on SSH, Tailscale or network rules.

1. Confirm that the agent runs (`sudo systemctl status snap.amazon-ssm-agent.amazon-ssm-agent.service`, or `amazon-ssm-agent` when it is not a snap). If it is missing: `sudo snap install amazon-ssm-agent --classic`.
2. Create the minimal role, attach it and install the session-manager plugin: follow `references/ssm-escape-hatch.md`, which holds both IAM policies and the exact commands.

**GATE:** the user runs `aws ssm start-session --target $INSTANCE_ID --region $REGION` from their laptop.

## Step 3: Tailscale

Install from the apt repo, not with `curl ... | sh`: apt checks each package against Tailscale's signing key, and a piped script runs as root unchecked.

```bash
curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/$(lsb_release -cs).noarmor.gpg \
  | sudo tee /usr/share/keyrings/tailscale-archive-keyring.gpg >/dev/null
curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/$(lsb_release -cs).tailscale-keyring.list \
  | sudo tee /etc/apt/sources.list.d/tailscale.list
sudo apt-get update && sudo apt-get install -y tailscale
sudo tailscale up
```

The user opens the auth URL. Then run `tailscale ip -4`.

**GATE:** the user SSHes to the Tailscale IP from their laptop or phone.

## Step 4: UFW

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow in on tailscale0          # all traffic over Tailscale
sudo ufw allow 41641/udp                 # WireGuard direct connections
sudo ufw --force enable
sudo ufw status verbose
```

The `tailscale0` rule covers SSH: you do not need a port 22 rule.

**GATE:** the user can still SSH to the Tailscale IP.

## Step 5: SSH and fail2ban

Ask the user first: do you use SFTP (VS Code Remote, scp, rsync over SSH)?

```bash
# Set based on user's answer:
SFTP_LINE="Subsystem sftp /bin/false"         # no SFTP
# SFTP_LINE="Subsystem sftp internal-sftp"    # if user needs SFTP

sudo tee /etc/ssh/sshd_config.d/99-hardening.conf << EOF
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

**GATE:** keep your current session open; the user opens a NEW SSH session to the Tailscale IP and confirms it works. A reload that breaks authentication locks out anyone who has no open session.

```bash
sudo rm -f /root/.ssh/authorized_keys   # PermitRootLogin no blocks root login; this also blocks it if that line is ever undone
sudo apt-get install -y fail2ban
sudo tee /etc/fail2ban/jail.local << 'EOF'
[sshd]
enabled = true
port = ssh
maxretry = 5
bantime = 600
findtime = 600
EOF
sudo systemctl enable --now fail2ban
```

If the user wants keyless SSH from a phone, set up Tailscale SSH: `references/tailscale-ssh.md`. It bypasses `sshd`, so nothing above applies to it.

## Step 6: Security group

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

**GATE:** the user confirms that SSH over Tailscale still works. Then `aws ec2 delete-security-group --group-id $OLD_SG_ID`.

## Step 7: OS hardening

Before you run this block, change the service list to match what Step 1 found running.

```bash
sudo tee /etc/sysctl.d/99-hardening.conf << 'EOF'
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.default.accept_redirects = 0
net.ipv4.conf.all.accept_source_route = 0
net.ipv4.conf.default.accept_source_route = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv4.conf.default.send_redirects = 0
net.ipv4.conf.all.log_martians = 1
net.ipv6.conf.all.accept_redirects = 0
net.ipv6.conf.default.accept_redirects = 0
EOF
sudo sysctl --system
aws ec2 modify-instance-metadata-options --instance-id $INSTANCE_ID \
  --http-tokens required --http-put-response-hop-limit 1   # IMDSv2 only, no hop to a container
for svc in ModemManager.service open-iscsi.service multipathd.service open-vm-tools.service; do
    if systemctl list-unit-files "$svc" &>/dev/null; then
        sudo systemctl disable --now "$svc"
    fi
done
sudo apt-get install -y unattended-upgrades
cat /etc/apt/apt.conf.d/20auto-upgrades  # should show "1" for both
```

Ask the user whether anything on the machine uses IPv6. If nothing does, also add `net.ipv6.conf.all.disable_ipv6 = 1`.

Add to the user's bashrc, so an abandoned shell closes and a pending reboot is visible:

```bash
if [ -z "${TMUX:-}" ]; then
    TMOUT=900
fi
if [ -f /var/run/reboot-required ]; then
    printf '\033[1;33m*** System restart required ***\033[0m\n'
    [ -f /var/run/reboot-required.pkgs ] && sed 's/^/    /' /var/run/reboot-required.pkgs
fi
```

## Step 8: Security review

Send the `cybersecurity-expert` agent (the `task` tool) this brief:

> Review the security posture of this EC2 instance. It is a personal development machine. Access is over Tailscale VPN, with SSM as an escape hatch. Review open ports, listening services, SSH config, firewall rules, IAM role scope, IMDS configuration and kernel params. Review permissions on sensitive files (~/.ssh, /etc/shadow, sudoers), AppArmor status, and Docker daemon config if installed. Report anything else concerning. Label each finding HIGH, MEDIUM or LOW risk, HIGH first.

If that agent is not available, do the review yourself. Act on every HIGH finding before Step 9.

## Step 9: Final verification

Check each change end to end and present the results as a summary table: UFW status, the effective `sshd -T` values, the fail2ban sshd jail, Tailscale status, the kernel params, IMDS options, listening ports, and the new security group's ingress rules.

## Common issues

| Problem | Fix |
|---------|-----|
| SSM agent does not register | Confirm that the instance has the IAM role, restart the agent, wait 30s |
| Tailscale auth link expires | `sudo tailscale up` again |
| Locked out after UFW enable | Connect over SSM, run `sudo ufw disable` |
| fail2ban bans you | `sudo fail2ban-client set sshd unbanip <YOUR_IP>` |
| SFTP clients break | Rerun Step 5 with `SFTP_LINE="Subsystem sftp internal-sftp"` |
