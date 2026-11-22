---
name: unlock-keyring
description: "Use when the devbox GNOME keyring is locked, or after a devbox reboot. Symptoms: `keyring.errors.KeyringLocked: Failed to unlock the collection!`, `hawk login` crashing at token storage after a successful login, or any Python `keyring` client failing to read or write a secret."
---

# Unlock the devbox keyring

This skill applies only to a machine with the `gnome-keyring` package. Check with `command -v gnome-keyring-daemon`.

- Expect `unlock-keyring` to say there is no keyring to unlock on a machine without the package.
- Log a tool that keeps its login in a keyring in again in each new shell on a machine without the package: the login ends with the shell that made it. There, the tool falls back to the kernel keyring, which is session-scoped.

## Unlock

1. **Ask Sami to unlock.** Ask him to run `unlock-keyring` (a function in `~/.dotfiles/.bashrc`) in a terminal on the box and type the password at the prompt: only Sami has the password.
   - **Why:** every reboot locks the `login` keyring. SSH login cannot unlock it: `ubuntu` has no Unix password, and only `/etc/pam.d/common-password` loads `pam_gnome_keyring.so`.

- **Never automate the password:** the secrets would sit unencrypted on disk.
- **Never bypass the lock with `PYTHON_KEYRING_BACKEND` or a plaintext backend:** the secrets would sit unencrypted on disk.
- **Never run `gnome-keyring-daemon --unlock` yourself:** it unlocks only a daemon it starts, so it does nothing while Ubuntu's unit runs. Each run also takes `/run/user/1000/keyring/control`. Each run deletes that directory when it exits. Clients then fail with `couldn't access control socket: No such file or directory`.
- **Have Sami stop both units Ubuntu ships first if he unlocks without the function:** `gnome-keyring-daemon.service` and `gnome-keyring-daemon.socket`. While Ubuntu's daemon runs, each `--unlock` logs `another secret service is running` and changes nothing.
- **Never pass `--start` with `--unlock`:** the two flags are incompatible. The working invocation is the function's `--unlock --components=pkcs11,secrets --daemonize`.

## Recovery

When the function reports the keyring still locked, or after a stray manual unlock:

1. **Kill the strays.** Kill each stray `gnome-keyring-daemon --unlock` by PID.
2. **Restart the socket.** Run `systemctl --user restart gnome-keyring-daemon.socket`.
3. **Ask Sami again.** Ask him to run `unlock-keyring` again.

## Relocks (about daily)

The unlocked daemon crashes (SIGABRT, a bug in Ubuntu's `gnome-keyring` package, not in a client). D-Bus then restarts Ubuntu's unit, which has no password. The crash signature in the journal is `GLib-GIO:ERROR:../../../gio/gdbusconnection.c:4749:invoke_get_property_in_idle_cb: assertion failed: (error != NULL)`.

- Unlock again.
- Never poll `busctl get-property ... Locked` in a loop: a D-Bus property read triggers the crash.
- Check `Locked` once after each unlock, for the same reason.
