# Step 7: OS hardening

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

Ask the user whether anything on the machine uses IPv6. If nothing on the machine uses IPv6, also add `net.ipv6.conf.all.disable_ipv6 = 1`. Add to the user's bashrc, so an idle shell outside tmux closes and a pending reboot shows:

```bash
[ -z "${TMUX:-}" ] && TMOUT=900
if [ -f /var/run/reboot-required ]; then
    printf '\033[1;33m*** System restart required ***\033[0m\n'
    [ -f /var/run/reboot-required.pkgs ] && sed 's/^/    /' /var/run/reboot-required.pkgs
fi
```
