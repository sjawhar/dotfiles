---
name: unlock-keyring
description: "Use when the devbox GNOME keyring is locked, or after a devbox reboot. Symptoms: `keyring.errors.KeyringLocked: Failed to unlock the collection!`, `hawk login` crashing at token storage after a successful login, or any Python `keyring` client failing to read or write a secret."
---

# Unlock the devbox keyring

## Unlock

1. Only Sami has the password: ask him to run `unlock-keyring` (a function in `.bashrc`) in a terminal on the box and type it at the prompt.
2. Never automate the password, and never bypass the lock with `PYTHON_KEYRING_BACKEND` or a plaintext backend: the secrets would sit unencrypted on disk.
3. Never run `gnome-keyring-daemon --unlock` yourself. It unlocks only a daemon it starts, so it does nothing while Ubuntu's unit runs, and each run takes `/run/user/1000/keyring/control` and deletes that directory when it exits; clients then fail with `couldn't access control socket: No such file or directory`.

## Recovery

When the function reports the keyring still locked, or after a stray manual unlock:

1. Kill each stray `gnome-keyring-daemon --unlock` by PID.
2. `systemctl --user restart gnome-keyring-daemon.socket`
3. Ask Sami to run `unlock-keyring` again.

## Relocks (about daily)

The unlocked daemon crashes (SIGABRT, a bug in Ubuntu's `gnome-keyring` package, not in a client), and D-Bus restarts Ubuntu's unit, which has no password. Unlock again. The journal shows `invoke_get_property_in_idle_cb: assertion failed: (error != NULL)`.

A D-Bus property read triggers the crash, so never poll `busctl get-property ... Locked` in a loop; check once after each unlock.
