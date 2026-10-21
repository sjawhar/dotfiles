---
name: bunker-down
description: Use when hardening a publicly reachable EC2 instance used as a daily development machine. Also use when adding Tailscale, a host firewall, SSH hardening or SSM access to one.
---

# Harden an EC2 devbox

- Never lock the user out. Open each new access path and confirm that it works before you close the old one.
- At each **GATE**, stop until the user confirms.
- Prerequisites: an authenticated AWS CLI (on the instance or the user's laptop), a Tailscale account for the user, and working SSH access now.

## Step 1: Assess

Run all of these. Present the findings as "good" or "concerning".

```bash
uname -a && cat /etc/os-release
ss -tlnp && ss -ulnp
sudo iptables -L -n && sudo ufw status && sudo nft list ruleset
sudo sshd -T | grep -iE 'permit|password|x11|maxauth|subsystem|clientalive'
cat /etc/ssh/sshd_config.d/*.conf 2>/dev/null
grep -vE 'nologin|/bin/false|/bin/sync' /etc/passwd
ls -la /root/.ssh/authorized_keys 2>/dev/null
sudo grep 'PermitRootLogin' /etc/ssh/sshd_config /etc/ssh/sshd_config.d/*.conf 2>/dev/null
dpkg -l | grep -iE 'fail2ban|unattended-upgrades|apparmor|auditd'
cat /etc/apt/apt.conf.d/20auto-upgrades
sysctl net.ipv4.ip_forward net.ipv4.conf.all.accept_redirects \
  net.ipv4.conf.all.accept_source_route net.ipv4.conf.all.send_redirects
sudo systemctl list-unit-files --state=enabled --type=service
TOKEN=$(curl -s -X PUT "http://169.254.169.254/latest/api/token" \
  -H "X-aws-ec2-metadata-token-ttl-seconds: 60") && \
curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/instance-id
aws ec2 describe-instances --instance-ids $INSTANCE_ID \
  --query 'Reservations[0].Instances[0].{SGs:SecurityGroups,IMDS:MetadataOptions,IAM:IamInstanceProfile}'
```

Collect these values:

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

1. Confirm that the agent runs. If it is missing: `sudo snap install amazon-ssm-agent --classic`.

```bash
sudo systemctl status snap.amazon-ssm-agent.amazon-ssm-agent.service
# or: sudo systemctl status amazon-ssm-agent
```

2. Do NOT use `AmazonSSMManagedInstanceCore`: it grants 15 or more actions. The policies below grant 5.
3. Before you apply them, have the `cybersecurity-expert` agent (the `task` tool) review both policies.

Trust policy (`aws:SourceAccount` blocks the confused-deputy problem):

```json
{"Version": "2012-10-17", "Statement": [{"Effect": "Allow",
  "Principal": {"Service": "ec2.amazonaws.com"}, "Action": "sts:AssumeRole",
  "Condition": {"StringEquals": {"aws:SourceAccount": "$ACCOUNT_ID"}}}]}
```

Inline policy (Session Manager only, explicit deny on all else):

```json
{"Version": "2012-10-17", "Statement": [
  {"Sid": "SSMAgentHeartbeat", "Effect": "Allow", "Action": ["ssm:UpdateInstanceInformation"], "Resource": "*"},
  {"Sid": "SessionManagerChannels", "Effect": "Allow", "Resource": "*", "Action": [
    "ssmmessages:CreateControlChannel", "ssmmessages:CreateDataChannel",
    "ssmmessages:OpenControlChannel", "ssmmessages:OpenDataChannel"]},
  {"Sid": "DenyEverythingExceptSessionManager", "Effect": "Deny", "Resource": "*", "NotAction": [
    "ssm:UpdateInstanceInformation",
    "ssmmessages:CreateControlChannel", "ssmmessages:CreateDataChannel",
    "ssmmessages:OpenControlChannel", "ssmmessages:OpenDataChannel"]}]}
```

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
sudo snap restart amazon-ssm-agent  # or sudo systemctl restart amazon-ssm-agent
sleep 15
aws ssm describe-instance-information --filters Key=InstanceIds,Values=$INSTANCE_ID
curl -so /tmp/session-manager-plugin.deb \
  "https://s3.amazonaws.com/session-manager-downloads/plugin/latest/ubuntu_64bit/session-manager-plugin.deb"
sudo dpkg -i /tmp/session-manager-plugin.deb && rm /tmp/session-manager-plugin.deb
```

**GATE:** the user runs `aws ssm start-session --target $INSTANCE_ID --region $REGION` from their laptop.

## Step 3: Tailscale

Install from the apt repo, not with curl-pipe-sh (supply-chain safety):

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
sudo rm -f /root/.ssh/authorized_keys
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

Optional, for keyless SSH from mobile: `sudo tailscale up --ssh`, then add this SSH ACL in the Tailscale admin console:

```json
"ssh": [{"action": "check", "src": ["autogroup:member"], "dst": ["autogroup:self"], "users": ["autogroup:nonroot"]}]
```

Tailscale SSH bypasses `sshd`, so the `sshd` hardening and fail2ban do not apply to it. Only Tailscale ACLs control it.

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
  --http-tokens required --http-put-response-hop-limit 1
for svc in ModemManager.service open-iscsi.service multipathd.service open-vm-tools.service; do
    if systemctl list-unit-files "$svc" &>/dev/null; then
        sudo systemctl disable --now "$svc"
    fi
done
sudo apt-get install -y unattended-upgrades
cat /etc/apt/apt.conf.d/20auto-upgrades  # should show "1" for both
```

- If the machine does not need IPv6, also add `net.ipv6.conf.all.disable_ipv6 = 1`.
- IMDS: the `modify-instance-metadata-options` line enforces IMDSv2 with hop limit 1.
- Add to the user's bashrc:

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

## Step 8: Security review

Send the `cybersecurity-expert` agent (the `task` tool) this brief:

> Review the security posture of this EC2 instance. It is a personal development machine. Access is over Tailscale VPN, with SSM as an escape hatch. Review open ports, listening services, SSH config, firewall rules, IAM role scope, IMDS configuration and kernel params. Review permissions on sensitive files (~/.ssh, /etc/shadow, sudoers), AppArmor status, and Docker daemon config if installed. Report anything else concerning. Prioritize by risk.

If that agent is not available, review the Step 9 output yourself. Act on every HIGH finding before Step 9.

## Step 9: Final verification

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

Present the results as a summary table.

## Common issues

| Problem | Fix |
|---------|-----|
| SSM agent does not register | Confirm that the instance has the IAM role, restart the agent, wait 30s |
| Tailscale auth link expires | `sudo tailscale up` again |
| Locked out after UFW enable | Connect over SSM, run `sudo ufw disable` |
| fail2ban bans you | `sudo fail2ban-client set sshd unbanip <YOUR_IP>` |
| SFTP clients break | Rerun Step 5 with `SFTP_LINE="Subsystem sftp internal-sftp"` |
