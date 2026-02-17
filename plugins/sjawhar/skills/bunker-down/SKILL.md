---
description: Harden an EC2 instance used as a development machine
---

# Harden EC2 Devbox

Security hardening for a publicly accessible EC2 instance used as a daily driver development machine. Establishes Tailscale VPN, host firewall, SSH hardening, and SSM escape hatch.

**Safety principle:** Never lock the user out. Establish escape hatches before restricting access. Confirm each access path works before closing the previous one.

## Quick Reference

| Step | Purpose | Gate |
|------|---------|------|
| 1 | Assess current security posture | Present findings |
| 2 | SSM escape hatch (out-of-band access) | User verifies SSM from laptop |
| 3 | Tailscale VPN | User SSHes over Tailscale |
| 4 | UFW firewall (deny all except Tailscale) | User SSHes over Tailscale |
| 5 | SSH hardening + fail2ban | — |
| 6 | Security group (WireGuard UDP only) | User confirms Tailscale works |
| 7 | OS hardening (kernel, IMDS, services) | — |
| 8 | Security audit via cybersecurity-expert agent | Fix HIGH findings |
| 9 | Final verification | Present summary |

## Prerequisites

- AWS CLI authenticated (on instance or user's laptop)
- User has a Tailscale account
- User has SSH access to the machine right now

## Variables

Collect during Step 1:

| Variable | Source |
|----------|--------|
| `$INSTANCE_ID` | Instance metadata or `aws ec2 describe-instances` |
| `$REGION` | Instance metadata |
| `$ACCOUNT_ID` | `aws sts get-caller-identity` |
| `$VPC_ID` | `aws ec2 describe-instances` |
| `$OLD_SG_ID` | Current security group on the instance |
| `$TAILSCALE_IP` | `tailscale ip -4` after Step 3 |

## Step 1: Assess Current State

Run all of these and present findings as "good" vs "concerning":

```bash
# OS and kernel
uname -a && cat /etc/os-release

# Listening ports
ss -tlnp && ss -ulnp

# Firewall state
sudo iptables -L -n && sudo ufw status && sudo nft list ruleset

# SSH config
sudo sshd -T | grep -iE 'permit|password|x11|maxauth|subsystem|clientalive'
cat /etc/ssh/sshd_config.d/*.conf 2>/dev/null

# User accounts with login shells
grep -vE 'nologin|/bin/false|/bin/sync' /etc/passwd

# Root SSH access
ls -la /root/.ssh/authorized_keys 2>/dev/null
sudo grep 'PermitRootLogin' /etc/ssh/sshd_config /etc/ssh/sshd_config.d/*.conf 2>/dev/null

# Security tools
dpkg -l | grep -iE 'fail2ban|unattended-upgrades|apparmor|auditd'

# Auto-update config
cat /etc/apt/apt.conf.d/20auto-upgrades

# Kernel network params
sysctl net.ipv4.ip_forward net.ipv4.conf.all.accept_redirects \
  net.ipv4.conf.all.accept_source_route net.ipv4.conf.all.send_redirects

# Enabled services
sudo systemctl list-unit-files --state=enabled --type=service

# IMDS
TOKEN=$(curl -s -X PUT "http://169.254.169.254/latest/api/token" \
  -H "X-aws-ec2-metadata-token-ttl-seconds: 60") && \
curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/instance-id
```

Instance details:

```bash
aws ec2 describe-instances --instance-ids $INSTANCE_ID \
  --query 'Reservations[0].Instances[0].{SGs:SecurityGroups,IMDS:MetadataOptions,IAM:IamInstanceProfile}'
```

## Step 2: SSM Escape Hatch

**Do this FIRST.** SSM provides out-of-band access independent of SSH, Tailscale, or network rules.

Check if SSM agent is running:

```bash
sudo systemctl status snap.amazon-ssm-agent.amazon-ssm-agent.service
# or: sudo systemctl status amazon-ssm-agent
```

If not installed: `sudo snap install amazon-ssm-agent --classic`

Create a minimal IAM role. **Do NOT use `AmazonSSMManagedInstanceCore`** — it grants ~15+ actions. The policy below grants 5.

Use the cybersecurity-expert agent to review the policies before applying.

Trust policy (with confused deputy protection):
```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Service": "ec2.amazonaws.com"},
    "Action": "sts:AssumeRole",
    "Condition": {
      "StringEquals": {"aws:SourceAccount": "$ACCOUNT_ID"}
    }
  }]
}
```

Inline policy — Session Manager only (explicit deny on everything else):
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "SSMAgentHeartbeat",
      "Effect": "Allow",
      "Action": ["ssm:UpdateInstanceInformation"],
      "Resource": "*"
    },
    {
      "Sid": "SessionManagerChannels",
      "Effect": "Allow",
      "Action": [
        "ssmmessages:CreateControlChannel",
        "ssmmessages:CreateDataChannel",
        "ssmmessages:OpenControlChannel",
        "ssmmessages:OpenDataChannel"
      ],
      "Resource": "*"
    },
    {
      "Sid": "DenyEverythingExceptSessionManager",
      "Effect": "Deny",
      "NotAction": [
        "ssm:UpdateInstanceInformation",
        "ssmmessages:CreateControlChannel",
        "ssmmessages:CreateDataChannel",
        "ssmmessages:OpenControlChannel",
        "ssmmessages:OpenDataChannel"
      ],
      "Resource": "*"
    }
  ]
}
```

Create role, instance profile, and associate:

```bash
aws iam create-role --role-name EC2-SSM-SessionOnly \
  --assume-role-policy-document '<trust_policy_json>' \
  --tags Key=purpose,Value=ssm-session-only
aws iam put-role-policy --role-name EC2-SSM-SessionOnly \
  --policy-name SSMSessionManagerMinimal \
  --policy-document '<inline_policy_json>'
aws iam create-instance-profile --instance-profile-name EC2-SSM-SessionOnly
aws iam add-role-to-instance-profile --instance-profile-name EC2-SSM-SessionOnly \
  --role-name EC2-SSM-SessionOnly
sleep 10  # IAM propagation
aws ec2 associate-iam-instance-profile --instance-id $INSTANCE_ID \
  --iam-instance-profile Name=EC2-SSM-SessionOnly
```

Restart agent and verify:

```bash
sudo snap restart amazon-ssm-agent  # or sudo systemctl restart amazon-ssm-agent
sleep 15
aws ssm describe-instance-information --filters Key=InstanceIds,Values=$INSTANCE_ID
```

Install Session Manager plugin:

```bash
curl -so /tmp/session-manager-plugin.deb \
  "https://s3.amazonaws.com/session-manager-downloads/plugin/latest/ubuntu_64bit/session-manager-plugin.deb"
sudo dpkg -i /tmp/session-manager-plugin.deb && rm /tmp/session-manager-plugin.deb
```

**GATE:** Ask user to verify SSM works from their laptop:
```bash
aws ssm start-session --target $INSTANCE_ID --region $REGION
```

## Step 3: Tailscale VPN

Install via apt repo (not curl-pipe-sh, for supply chain safety):

```bash
curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/$(lsb_release -cs).noarmor.gpg \
  | sudo tee /usr/share/keyrings/tailscale-archive-keyring.gpg >/dev/null
curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/$(lsb_release -cs).tailscale-keyring.list \
  | sudo tee /etc/apt/sources.list.d/tailscale.list
sudo apt-get update && sudo apt-get install -y tailscale
sudo tailscale up
```

User must open the auth URL. After success: `tailscale ip -4`

**GATE:** Ask user to confirm they can SSH to the Tailscale IP from their laptop/phone.

## Step 4: UFW Firewall

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow in on tailscale0          # all traffic over Tailscale
sudo ufw allow 41641/udp                 # WireGuard direct connections
sudo ufw --force enable
sudo ufw status verbose
```

The `tailscale0` rule covers SSH — no separate port 22 rule needed.

**GATE:** Verify user can still SSH to the Tailscale IP.

## Step 5: SSH Hardening

**Ask the user first:** Do you use SFTP (VS Code Remote, scp, rsync over SSH)?

Create drop-in config:

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

Remove root's authorized keys:

```bash
sudo rm -f /root/.ssh/authorized_keys
```

Install fail2ban:

```bash
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

**Optional — Tailscale SSH** (keyless SSH from mobile):

```bash
sudo tailscale up --ssh
```

Configure SSH ACL in Tailscale admin console:
```json
"ssh": [{
  "action": "check",
  "src": ["autogroup:member"],
  "dst": ["autogroup:self"],
  "users": ["autogroup:nonroot"]
}]
```

**Note:** Tailscale SSH bypasses `sshd` entirely — the hardening above and fail2ban do NOT apply to Tailscale SSH connections. Access control is via Tailscale ACLs only.

## Step 6: Security Group Lockdown

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

**GATE:** Ask user to confirm Tailscale SSH still works, then delete the old SG:

```bash
aws ec2 delete-security-group --group-id $OLD_SG_ID
```

## Step 7: OS Hardening

Kernel network params:

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
```

If IPv6 is not needed, also add `net.ipv6.conf.all.disable_ipv6 = 1`.

IMDS hardening (enforce v2, hop limit 1):

```bash
aws ec2 modify-instance-metadata-options --instance-id $INSTANCE_ID \
  --http-tokens required --http-put-response-hop-limit 1
```

Disable unnecessary services (adapt based on what's running):

```bash
for svc in ModemManager.service open-iscsi.service multipathd.service open-vm-tools.service; do
    if systemctl list-unit-files "$svc" &>/dev/null; then
        sudo systemctl disable --now "$svc"
    fi
done
```

Shell hardening — add to user's bashrc:

```bash
# Idle timeout (skip inside tmux)
if [ -z "${TMUX:-}" ]; then
    TMOUT=900
fi

# Reboot-required notification
if [ -f /var/run/reboot-required ]; then
    printf '\033[1;33m*** System restart required ***\033[0m\n'
    [ -f /var/run/reboot-required.pkgs ] && sed 's/^/    /' /var/run/reboot-required.pkgs
fi
```

Ensure unattended-upgrades:

```bash
sudo apt-get install -y unattended-upgrades
cat /etc/apt/apt.conf.d/20auto-upgrades  # should show "1" for both
```

## Step 8: Security Assessment

Dispatch the cybersecurity-expert agent to review the final state:

> Review the security posture of this EC2 instance. It's a personal development machine accessed via Tailscale VPN with SSM as an escape hatch. Check for: open ports, listening services, SSH config, firewall rules, IAM role scope, IMDS configuration, kernel params, file permissions on sensitive files (~/.ssh, /etc/shadow, sudoers), AppArmor status, Docker daemon config (if installed), and anything else concerning. Prioritize by risk.

If the cybersecurity-expert agent is not available, manually review the Step 9 output.

Act on any HIGH findings before proceeding.

## Step 9: Final Verification

```bash
echo "=== UFW ===" && sudo ufw status numbered
echo "=== SSHD ===" && sudo sshd -T | grep -iE 'permitroot|passwordauth|kbdinteractive|authenticationmethods|x11|maxauth|logingrace|clientalive|subsystem|allowagent|permittunnel'
echo "=== fail2ban ===" && sudo fail2ban-client status sshd
echo "=== Tailscale ===" && tailscale status
echo "=== Kernel ===" && sysctl net.ipv4.conf.all.send_redirects net.ipv4.conf.all.log_martians net.ipv4.conf.all.accept_redirects net.ipv4.conf.all.accept_source_route
echo "=== IMDS ===" && aws ec2 describe-instances --instance-ids $INSTANCE_ID \
  --query 'Reservations[0].Instances[0].MetadataOptions.{HttpTokens:HttpTokens,HopLimit:HttpPutResponseHopLimit}' --output table
echo "=== Listening ports ===" && ss -tlnp
echo "=== SG ===" && aws ec2 describe-security-group-rules \
  --filter Name=group-id,Values=$SG_ID --query 'SecurityGroupRules[?IsEgress==`false`]' --output table
```

Present results as a summary table.

## Common Issues

| Problem | Fix |
|---------|-----|
| SSM agent won't register | Check IAM role attached, restart agent, wait 30s |
| Tailscale auth link expires | Run `sudo tailscale up` again |
| Locked out after UFW enable | Connect via SSM, run `sudo ufw disable` |
| fail2ban bans you | `sudo fail2ban-client set sshd unbanip <YOUR_IP>` |
| SFTP clients break | Rerun Step 5 with `SFTP_LINE="Subsystem sftp internal-sftp"` |
