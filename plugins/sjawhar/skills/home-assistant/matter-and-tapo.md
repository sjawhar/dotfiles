# Matter and Tapo devices

## HA WebSocket API

- Some UI-only actions have no MCP tool or service, but have a websocket command. Known: `matter/interview_node` (device page "Re-interview"), `matter/ping_node`, `matter/node_diagnostics`, each taking `device_id`.
- No `matter.*` service re-interviews, and the Matter Server's port 5580 is not on the LAN.
- Pattern: `secrets HA_TOKEN -- bun <script>`. Connect to `ws://homeassistant.local:8123/api/websocket`, send `auth` with `access_token`, then `{id, type, ...}`.
- `ha_get_integration(include_diagnostics=True, device_id=…)` returns the attribute cache as of the last interview, not live values. Re-interview first for current values.

## Endpoint layout change after a firmware update

- Re-interview drops every entity on the node and runs discovery again. The in-memory discovered-keys set survives, so entities whose key existed before are NOT re-created (switches, selects, update, identify). Only sensors whose endpoint numbers changed come back.
- Fix: `ha_call_service` `homeassistant.reload_config_entry`, `data.entry_id` = the Matter entry. The registry restores the original IDs and names. Re-created sensors get new generated IDs, so rename them again.

## Tapo firmware versions

- Matter `SoftwareVersionString` (`0/40/10`, shown by HA's `update.*`) is not the Tapo firmware version. TP-Link leaves it at "1.3.0". HA's "latest" comes from the Matter DCL, which lags TP-Link.
- Pre-1.4 firmware: `uvx --from python-kasa kasa --host <ip> --debug discover` → `fw_ver`, no credentials. 1.4.x uses the `TPAP` local protocol, which python-kasa does not support (as of Sep 2026). Then only the Tapo app shows it.
- Compare with `https://www.tp-link.com/us/support/download/<model>/` → "Firmware Release Note".
- Updates install only through the Tapo app. Expect an endpoint change and the reload above.

## Devices

- Tapo P316M power strip:
  - fw 1.4.1: one endpoint per socket (1–6) with plug and metering clusters. Imported is per socket, exported is 0, and energy reports live. Use the native sensors, no Riemann helpers.
  - fw 1.0.5: switches on 1–6, metering on 7–12. `CumulativeEnergyImported` (`<ep>/145/1`) is the same on every endpoint. Per-socket kWh is in `CumulativeEnergyExported` (`<ep>/145/2`). Energy changes only on re-interview.
  - Always confirm endpoint→socket from the metering endpoint's PowerTopology cluster (`<ep>/156/0` → `[socket]`). HA names ("Power (7)") use endpoint numbers.
  - Power, voltage and current report about every 10 s on both firmwares.
- Eve Energy 20ECN4101 dual outlet: one combined reading for both sockets on Matter endpoint 1 (node device). HA's "(top)" suffix is a naming artifact, not per-socket metering.
