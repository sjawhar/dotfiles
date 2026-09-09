---
name: unlock-keyring
description: "Use when the devbox GNOME keyring is locked — `keyring.errors.KeyringLocked: Failed to unlock the collection!`, `hawk login` crashing at token storage after an otherwise successful login, or any Python `keyring` client failing to read or write a secret. Every devbox reboot leaves it locked."
---

# Unlock the devbox keyring

The `ubuntu` account has no Unix password and `pam_gnome_keyring.so` is wired only into
`common-password`, so nothing unlocks the `login` keyring at SSH login. It comes up locked
after every reboot and stays locked until this is run.

```bash
systemctl --user stop gnome-keyring-daemon.service gnome-keyring-daemon.socket
read -rsp 'Keyring password: ' KP && printf '%s' "$KP" | \
  gnome-keyring-daemon --unlock --components=pkcs11,secrets --daemonize; unset KP; echo
busctl --user get-property org.freedesktop.secrets \
  /org/freedesktop/secrets/collection/login \
  org.freedesktop.Secret.Collection Locked   # must print: b false
```

Only Sami has the password. Ask him and let him type it; never automate it, and never
route around a locked keyring with `PYTHON_KEYRING_BACKEND` or a plaintext backend.

Stopping the stock units first is load-bearing. `--unlock` is a startup flag: it unlocks
the login keyring only in a daemon it starts itself, and never hands a password to one
already running. `gnome-keyring-daemon.service` owns `org.freedesktop.secrets` but can
never unlock it, so while it runs every attempt logs `another secret service is running`
and changes nothing. `--start` is rejected as incompatible with `--unlock`.

Never retry with a bare `gnome-keyring-daemon --unlock`. With another daemon holding the
bus name it daemonizes instead, rebinds `/run/user/1000/keyring/control` to its own socket,
and unlinks the control directory on exit — after a few, every client fails with
`couldn't access control socket: No such file or directory`. Recover by killing each stray
`gnome-keyring-daemon --unlock` by PID, `systemctl --user restart
gnome-keyring-daemon.socket`, then running the procedure above.

## Why it relocks, roughly daily

A relock is the unlocked daemon **crashing**. Established 2026-09-09 from a captured core:
the `--replace --daemonize --unlock` daemon dies with SIGABRT, and D-Bus activation brings
back `gnome-keyring-daemon --start --foreground --components=secrets` (the stock
`gnome-keyring-daemon.service`), which was never given the password, so the `login`
collection reads `Locked = true` and every client fails with `KeyringLocked`.

The abort is in the Ubuntu `gnome-keyring` package (46.1-2ubuntu0.2), not in any client:

```
GLib-GIO:ERROR:../../../gio/gdbusconnection.c:4749:invoke_get_property_in_idle_cb:
  assertion failed: (error != NULL)
```

`invoke_get_property_in_idle_cb` runs only while serving a client's D-Bus
`Properties.Get`/`GetAll`. GLib requires a registered object's `get_property` vfunc to set a
`GError` when it returns NULL; gnome-keyring's secret-service getter returns NULL without
one for at least one path/property, and GLib aborts the process.

So **never put the `busctl get-property … Locked` check in a watch loop** — a property read
is the exact call that aborts, and a poll every few seconds multiplies the chance of hitting
it. One check after an unlock is what the procedure above is for. A 15-second poll ran from
2026-09-03 to 2026-09-09 and saw six relocks in six days, each within ~20 s of a daemon exit.

Note that apport keeps one report per binary, so the first crash suppresses reports for every
later one. If `/var/crash/_usr_bin_gnome-keyring-daemon.1000.crash` exists, move it aside
before expecting a fresh report. Read one with
`apport-unpack <file> <dir>` then `gdb -q -batch -ex 'p (char *) __glib_assert_msg'
-ex 'bt 12' /usr/bin/gnome-keyring-daemon <dir>/CoreDump`.
