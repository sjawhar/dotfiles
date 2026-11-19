---
name: bunker-down
description: Use when hardening a publicly reachable EC2 instance used as a daily development machine. Also use when adding Tailscale, a host firewall, SSH hardening or SSM access to one.
---

# Harden an EC2 devbox

Apply each setting below even where the box's current config is looser, and tell the user each one you change.

- Open each new access path and confirm it works before you close the old one, so the user is never locked out.
- At each GATE, stop until the user confirms.
- Needs: an authenticated AWS CLI (on the box or the user's laptop), the user's Tailscale account, and working SSH now.

## 1. Assess

Report each item as good or concerning: OS release and patch state, listening sockets, firewall (ufw, iptables, nftables), `sshd -T` and every file in `/etc/ssh/sshd_config.d/`, login-capable accounts, `/root/.ssh/authorized_keys`, fail2ban, unattended-upgrades, AppArmor, auditd, network sysctls, enabled services, published Docker ports, and from AWS the security groups, IMDS options and IAM instance profile. Record for later steps: `INSTANCE_ID` and `REGION` (instance metadata), `ACCOUNT_ID` (`aws sts get-caller-identity`), `OLD_SG_ID` (the instance's current security group).

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

Ask the user whether they use SFTP (VS Code Remote, scp, rsync over SSH).

```bash
SFTP_LINE="Subsystem sftp /bin/false"         # no SFTP
# SFTP_LINE="Subsystem sftp internal-sftp"    # the user needs SFTP
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

```bash
{ printf '%s = 0\n' net.ipv4.conf.{all,default}.{accept_redirects,accept_source_route,send_redirects} \
    net.ipv6.conf.{all,default}.accept_redirects
  echo 'net.ipv4.conf.all.log_martians = 1'; } | sudo tee /etc/sysctl.d/99-hardening.conf
sudo sysctl --system
aws ec2 modify-instance-metadata-options --instance-id $INSTANCE_ID \
  --http-tokens required --http-put-response-hop-limit 1   # IMDSv2 only, no hop to a container
# Edit this list to what Step 1 found running:
for svc in ModemManager.service open-iscsi.service multipathd.service open-vm-tools.service; do
    systemctl list-unit-files "$svc" &>/dev/null && sudo systemctl disable --now "$svc"
done
sudo apt-get install -y unattended-upgrades
cat /etc/apt/apt.conf.d/20auto-upgrades  # both values should be "1"
```

If nothing on the machine uses IPv6 (ask the user), also add `net.ipv6.conf.all.disable_ipv6 = 1`. Add to the user's bashrc, so an idle shell outside tmux closes and a pending reboot shows:

```bash
[ -z "${TMUX:-}" ] && TMOUT=900
if [ -f /var/run/reboot-required ]; then
    printf '\033[1;33m*** System restart required ***\033[0m\n'
    [ -f /var/run/reboot-required.pkgs ] && sed 's/^/    /' /var/run/reboot-required.pkgs
fi
```

## 8. Security review

Give the `cybersecurity-expert` agent this brief, or do the review yourself if it is not available, and fix every HIGH finding before Step 9:

> Review the security posture of this EC2 instance, a personal development machine reached over Tailscale with SSM as an escape hatch: open ports, listening services, SSH config, firewall rules, IAM role scope, IMDS options, kernel params, permissions on ~/.ssh, /etc/shadow and sudoers, AppArmor, and the Docker daemon config if installed. Label each finding HIGH, MEDIUM or LOW, HIGH first.

## 9. Final verification

Check each change end to end and give the user a summary table: UFW status, the effective `sshd -T` values, the fail2ban sshd jail, Tailscale status, kernel params, IMDS options, listening ports, and the new security group's ingress rules.

## Common issues

- SSM agent does not register: check the instance has the IAM role, restart the agent, wait 30 s.
- Tailscale auth link expired: `sudo tailscale up` again.
- Locked out after `ufw enable`: connect over SSM and run `sudo ufw disable`.
- fail2ban banned you: `sudo fail2ban-client set sshd unbanip <YOUR_IP>`.
- SFTP clients break: rerun Step 5 with `SFTP_LINE="Subsystem sftp internal-sftp"`.
