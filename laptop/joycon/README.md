# joycon — Nintendo Joy-Cons for Amazon Luna (browser), with rumble

On-demand Bluetooth Joy-Con support for cloud gaming in Brave: correct button
mapping **and** working force-feedback, without the input freeze.

## The problems this solves

1. **Wrong button mapping.** [`joycond`](https://github.com/DanielOgorchock/joycond)
   combines the two Joy-Cons into one virtual device, but Brave/Chromium doesn't
   recognize its made-up USB id (`057e:2008`), so it indexes buttons by raw
   evdev-code order. The Joy-Cons' extra capture (`BTN_Z`) and home (`BTN_MODE`)
   buttons plus a missing `BTN_C` shift every button off the standard layout.
2. **Input freeze on rumble.** A plain event-remapper (e.g. evsieve) advertises
   force-feedback but can't *service* it, so Chromium's rumble upload stalls the
   gamepad thread — intermittent input freezes.

## How it works

[`joycon-remap.c`](joycon-remap.c) is a small C program (libc only). It grabs
joycond's combined device and re-emits a virtual gamepad that:

- **Remaps three codes** so Chromium's raw-index order becomes the standard 0–15
  layout: `BTN_WEST→BTN_C` (left face → index 2), `BTN_Z→BTN_TRIGGER_HAPPY5`
  (capture out of index 4), `BTN_MODE→BTN_TRIGGER_HAPPY6` (home out of the
  stick-click slots).
- **Forwards force-feedback**: it services the virtual device's FF upload/erase/
  play requests and forwards them to the combined device, which joycond drives to
  the physical Joy-Con motors. So rumble works and never stalls.

The virtual device keeps id `057e:2008` and a name containing "Virtual", so
joycond ignores it (its own `*Virtual*` exclusion) and doesn't re-grab it.

## Usage

```
joycon on       # start joycond + arm the remap path-watcher; press L+R anytime
joycon off      # stop everything (nothing runs when off)
joycon status   # show what's active
```

`joycon on` arms a systemd `.path` watcher, so pressing **L+R** at any time makes
the corrected controller ("Joy-Cons Luna Virtual") appear automatically. Select
it in Luna.

## Layout

- `../../scripts/joycon` — the `joycon on/off/status` command (on PATH)
- `joycon-remap.c` — the remapper + FF forwarder (built to `/usr/local/bin/joycon-remap`)
- `joycon-remap.service` — runs it (system service; needs `/dev/uinput`)
- `joycon-remap.path` — starts the service when `/dev/input/joycon-combined` appears
- `95-joycon-remap.rules` — uaccess for the output + stable symlink for the combined device
- `../joycon.sh` — installer: builds joycond from source (pinned), compiles the C remapper, deploys units + rule, disables joycond autostart

## Notes

- `joycond` autostart is disabled on purpose (nothing runs until `joycon on`).
  After a reboot, run `joycon on`.
- Building the remapper is just `cc joycon-remap.c` — no toolchain beyond a C compiler.
- `joycond` isn't packaged for Ubuntu/Pop, so the installer builds + installs it
  from upstream (pinned to a known-good commit). Nothing depends on a local
  `~/Code/joycond` clone — the whole setup reproduces from this repo alone.
