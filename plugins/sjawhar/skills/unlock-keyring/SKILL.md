---
name: unlock-keyring
description: "Use when the devbox GNOME keyring is locked, or after a devbox reboot. Symptoms: `keyring.errors.KeyringLocked: Failed to unlock the collection!`, `hawk login` crashing at token storage after a successful login, or any Python `keyring` client failing to read or write a secret."
---

# Unlock the devbox keyring

Every reboot locks the `login` keyring. SSH login cannot unlock it: `ubuntu` has no Unix password, and only `/etc/pam.d/common-password` loads `pam_gnome_keyring.so`.

This skill applies only to a machine with the `gnome-keyring` package; check with `command -v gnome-keyring-daemon`. A machine without it has no keyring to unlock, and `unlock-keyring` says so. There, a tool that keeps its login in a keyring falls back to the kernel keyring, which is session-scoped: the login ends with the shell that made it, and the tool needs its own login again.

## Unlock

1. Only Sami has the password: ask him to run `unlock-keyring` (a function in `~/.dotfiles/.bashrc`) in a terminal on the box and type it at the prompt.
2. Never automate the password, and never bypass the lock with `PYTHON_KEYRING_BACKEND` or a plaintext backend: the secrets would sit unencrypted on disk.
3. Never run `gnome-keyring-daemon --unlock` yourself. It unlocks only a daemon it starts, so it does nothing while Ubuntu's unit runs, and each run takes `/run/user/1000/keyring/control` and deletes that directory when it exits; clients then fail with `couldn't access control socket: No such file or directory`.
4. If Sami unlocks without the function, he must first stop both units Ubuntu ships, `gnome-keyring-daemon.service` and `gnome-keyring-daemon.socket`: while Ubuntu's daemon runs, each `--unlock` logs `another secret service is running` and changes nothing. Never pass `--start` with `--unlock`: the two flags are incompatible. The working invocation is the function's `--unlock --components=pkcs11,secrets --daemonize`.

## Recovery

When the function reports the keyring still locked, or after a stray manual unlock:

1. Kill each stray `gnome-keyring-daemon --unlock` by PID.
2. `systemctl --user restart gnome-keyring-daemon.socket`
3. Ask Sami to run `unlock-keyring` again.

## Relocks (about daily)

The unlocked daemon crashes (SIGABRT, a bug in Ubuntu's `gnome-keyring` package, not in a client), and D-Bus restarts Ubuntu's unit, which has no password. Unlock again. The crash signature in the journal is `GLib-GIO:ERROR:../../../gio/gdbusconnection.c:4749:invoke_get_property_in_idle_cb: assertion failed: (error != NULL)`.

A D-Bus property read triggers the crash, so never poll `busctl get-property ... Locked` in a loop; check once after each unlock.
