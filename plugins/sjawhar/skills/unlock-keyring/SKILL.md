---
name: unlock-keyring
description: "Use when the devbox GNOME keyring is locked, or after a devbox reboot. Symptoms: `keyring.errors.KeyringLocked: Failed to unlock the collection!`, `hawk login` crashing at token storage after a successful login, or any Python `keyring` client failing to read or write a secret."
---

# Unlock the devbox keyring

**Done when:** you check `busctl get-property ... Locked` once after each unlock.

Every reboot locks the `login` keyring. SSH login cannot unlock it: `ubuntu` has no Unix password, and only `/etc/pam.d/common-password` loads `pam_gnome_keyring.so`.

This skill applies only to a machine with the `gnome-keyring` package. Check with `command -v gnome-keyring-daemon`. A machine without it has no keyring to unlock, and `unlock-keyring` says so. There, a tool that keeps its login in a keyring falls back to the kernel keyring. The kernel keyring is session-scoped: the login ends with the shell that made it. The tool then needs its own login again.

## Unlock

1. **Ask Sami to unlock.** Ask him to run `unlock-keyring` (a function in `~/.dotfiles/.bashrc`) in a terminal on the box and type the password at the prompt: only Sami has the password.
2. **Never automate the password:** the secrets would sit unencrypted on disk.
3. **Never bypass the lock with `PYTHON_KEYRING_BACKEND` or a plaintext backend:** the secrets would sit unencrypted on disk.
4. **Never run `gnome-keyring-daemon --unlock` yourself.** It unlocks only a daemon it starts, so it does nothing while Ubuntu's unit runs. Each run takes `/run/user/1000/keyring/control` and deletes that directory when it exits. Clients then fail with `couldn't access control socket: No such file or directory`.
5. **If Sami unlocks without the function:**
   - He must first stop both units Ubuntu ships, `gnome-keyring-daemon.service` and `gnome-keyring-daemon.socket`. While Ubuntu's daemon runs, each `--unlock` logs `another secret service is running` and changes nothing.
   - Never pass `--start` with `--unlock`: the two flags are incompatible. The working invocation is the function's `--unlock --components=pkcs11,secrets --daemonize`.

## Recovery

When the function reports the keyring still locked, or after a stray manual unlock:

1. **Kill the strays.** Kill each stray `gnome-keyring-daemon --unlock` by PID.
2. **Restart the socket.** Run `systemctl --user restart gnome-keyring-daemon.socket`.
3. **Ask Sami again.** Ask him to run `unlock-keyring` again.

## Relocks (about daily)

The unlocked daemon crashes. D-Bus then restarts Ubuntu's unit, which has no password.

| Crash | Detail |
| --- | --- |
| Signal | SIGABRT |
| Cause | A bug in Ubuntu's `gnome-keyring` package, not in a client |
| Signature in the journal | `GLib-GIO:ERROR:../../../gio/gdbusconnection.c:4749:invoke_get_property_in_idle_cb: assertion failed: (error != NULL)` |

- Unlock again.
- Never poll `busctl get-property ... Locked` in a loop: a D-Bus property read triggers the crash.
