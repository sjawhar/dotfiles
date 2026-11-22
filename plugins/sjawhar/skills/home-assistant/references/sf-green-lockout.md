# The SF Green's lockout

- Since 2026-11-20 the Green bans an address after 5 requests with invalid authentication (a wrong, expired or revoked token, or a failed login): Home Assistant then answers 403 to every request from that address on port 8123, ha-mcp included, until the address leaves `ip_bans.yaml` and Home Assistant restarts. SSH and the apps on their own ports still answer. The count is per address, and a request with a valid token never resets it; only a login through the login page, a websocket login from that address, or a restart does. Send the Green only a token you have seen work, and test refusals against a stub. Remote UI requests count against the client's public address.
- The threshold is in the http settings (Settings → System → Network), not YAML: websocket `http/config/configure` restarts Home Assistant on new settings, and `http/config/promote` within 5 minutes keeps them, else it restarts on the old ones. Remove a ban, then restart (automations pause about two minutes):

  ```sh
  ssh -i ~/.ssh/ha-dojo root@10.0.0.254 'a=<address>; f=/homeassistant/ip_bans.yaml; cp -p "$f" "$f.bak" && sed -i "/^${a//./\\.}:\$/,+1d" "$f" && cat "$f"'
  ssh -i ~/.ssh/ha-dojo root@10.0.0.254 'ha core restart'
  ```
