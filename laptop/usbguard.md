# USBGuard Policy

## Threat Model

Physical USB attacks — someone plugging a malicious device into an exposed port. USBGuard blocks all USB devices by default and only allows devices matching explicit rules.

## Policy Design

Layered, from most trusted to least:

1. **Internal devices** (webcam, fingerprint reader, Bluetooth) — pinned by `hash`, which uniquely identifies the physical device. These are soldered to the motherboard and never change topology.

2. **Known end-devices** (keyboard, mouse) — allowed by `id` (vendor:product) + `name` + `with-interface`. This matches the device model regardless of which hub or port it's behind. The `with-interface` constraint confirms the device presents expected USB interface classes (e.g., HID for keyboards) and not something unexpected like mass storage (a BadUSB indicator).

3. **Approved dock hubs** — pinned by `hash` + `with-interface match-all { 09:00:00 09:00:01 09:00:02 }`. The hash identifies the specific hub model. The `match-all` ensures the device's *entire* interface set is hub class — a composite device presenting hub + keyboard interfaces would be rejected. Allowing a hub does NOT automatically allow devices behind it; each downstream device is independently evaluated.

4. **Everything else** — blocked.

## Key Attributes and What They Mean

| Attribute | What it identifies | Portability |
|---|---|---|
| `id vendor:product` | Device model (by manufacturer-assigned IDs) | High — same model anywhere |
| `name "..."` | Human-readable device name from firmware | High — same model anywhere |
| `hash "..."` | Specific device model (computed from USB descriptors) | Medium — same model, any port |
| `with-interface` | USB interface classes the device exposes | High — inherent to the device |
| `parent-hash "..."` | The specific hub this device is behind | Low — breaks when you change docks |
| `via-port "..."` | Physical port path (e.g., `5-1.4.1`) | Low — breaks when you change ports |
| `with-connect-type` | Kernel's guess at connection type | Low — unreliable across docks |

**We intentionally omit `parent-hash`, `via-port`, and `with-connect-type`** from portable rules. These are topology attributes that tie a device to a specific physical position in the USB tree, which is exactly what breaks when you move to a new dock.

## Why Hubs Are Allowed by Hash, Not Vendor

USB hubs don't have serial numbers, so the `hash` is per-model, not per-unit. Two identical hubs off the shelf have the same hash. This is the tightest granularity USB allows without topology-locking (`parent-hash`).

We don't use vendor wildcards (e.g., `allow id 0424:*`) because hub chip vendors like Microchip/SMSC are in countless docks from different manufacturers. Allowing by vendor would mean anyone with a Dell/Lenovo/HP dock using the same chip vendor gets their hubs through.

## Why `generate-policy` Isn't Enough

`usbguard generate-policy` produces maximally strict rules: every device gets `hash`, `parent-hash`, and (for devices without serial numbers) `via-port`. This is secure but completely non-portable — changing docks, hubs, or even rebooting (unstable port numbering) can break rules.

The `-P` flag suppresses `via-port` and `-X` suppresses hashes, but there's no flag to suppress only `parent-hash` ([upstream issue #503](https://github.com/USBGuard/usbguard/issues/503)). Manual editing is the accepted workaround.

## New Dock Workflow

1. Plug in the new dock. All its hubs are blocked, so nothing behind it works.

2. List blocked devices:
   ```
   sudo usbguard list-devices | grep block
   ```

3. Allow each hub in the chain, one at a time:
   ```
   sudo usbguard allow-device <ID>
   ```
   As each hub is authorized, its downstream devices enumerate and are individually evaluated. Your keyboard and mouse auto-allow via their `id` rules. Unknown devices stay blocked.

4. Once satisfied, add the new hub hashes to `/etc/usbguard/rules.conf` to persist across reboots. For each blocked hub, copy the `hash` value from `list-devices` and add a rule:
   ```
   # Description of the dock/hub
   allow hash "<hash>" with-interface match-all { 09:00:00 09:00:01 09:00:02 }
   ```
   For non-standard dock controller hubs (vendor-specific interfaces like `ff:ff:ff`), allow by hash alone:
   ```
   allow hash "<hash>"  # 0424:7260 USB2 Controller Hub
   ```

5. Restart to verify:
   ```
   sudo systemctl restart usbguard
   sleep 3
   sudo usbguard list-devices | grep block
   ```

## Adding a New Personal Device

For a new keyboard, mouse, or similar:

1. Plug it in. It will be blocked.

2. Find it:
   ```
   sudo usbguard list-devices | grep block
   ```

3. Note the `id`, `name`, and `with-interface` values. Add a rule to `rules.conf`:
   ```
   allow id <vendor>:<product> name "<name>" with-interface one-of { <interfaces> }
   ```
   Use `one-of` so the device just needs to present at least one of the expected interfaces.

4. Restart USBGuard and verify.

## The Rules File

Lives at `/etc/usbguard/rules.conf` (permissions `0600 root:root`). Rules are evaluated sequentially — first match wins. The file is organized in sections:

1. Linux root hubs (kernel-internal)
2. Internal devices (pinned by hash)
3. Personal peripherals (portable by id + interface)
4. Dock hubs (pinned by model hash)
5. Dock non-hub controllers and peripherals
6. Default `block`

## Daemon Config

`/etc/usbguard/usbguard-daemon.conf` — notable settings:

- `DeviceRulesWithPort=false` — `allow-device -p` won't add `via-port` to generated rules (but still adds `parent-hash`, which you'll want to strip manually)
- `IPCAllowedGroups=root plugdev` — members of `plugdev` can run `usbguard` commands without sudo
