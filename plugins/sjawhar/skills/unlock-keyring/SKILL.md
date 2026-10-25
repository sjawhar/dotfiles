---
name: unlock-keyring
description: "Use when the devbox GNOME keyring is locked, or after a devbox reboot. Symptoms: `keyring.errors.KeyringLocked: Failed to unlock the collection!`, `hawk login` crashing at token storage after a successful login, or any Python `keyring` client failing to read or write a secret."
---

# Unlock the devbox keyring

If this skill disagrees with the code, a command's output or its `--help`, follow those and say which line of this skill is wrong.

Every reboot locks the `login` keyring. SSH login cannot unlock it: `ubuntu` has no Unix password, and only `/etc/pam.d/common-password` loads `pam_gnome_keyring.so`.

## Unlock

- Only Sami has the password. Ask him to run `unlock-keyring` in a terminal on the box and type it at the prompt. The function is in `~/.dotfiles/.bashrc`: it stops the units Ubuntu ships (`gnome-keyring-daemon.service` and `gnome-keyring-daemon.socket`), starts a daemon with the password, and prints `keyring unlocked` or the failure.
- Never automate the password, and never bypass the lock with `PYTHON_KEYRING_BACKEND` or a plaintext backend: a secret then sits unencrypted on disk.
- An unlock attempted without that function must stop both units first: `--unlock` unlocks only a daemon that it starts, so while Ubuntu's daemon runs each attempt logs `another secret service is running` and changes nothing. Never pass `--start` with `--unlock`: the two flags are incompatible, and the working invocation is the function's `--unlock --components=pkcs11,secrets --daemonize`.
- Never retry by running `gnome-keyring-daemon --unlock` yourself; unlock through the function. Each such run takes `/run/user/1000/keyring/control` and deletes the control directory when it exits, so clients fail with `couldn't access control socket: No such file or directory`. To recover: kill each stray `gnome-keyring-daemon --unlock` by PID, run `systemctl --user restart gnome-keyring-daemon.socket`, then unlock again.

## Relocks (about daily)

- A relock is a SIGABRT crash of the unlocked daemon, from a bug in Ubuntu's `gnome-keyring` package (46.1-2ubuntu0.2), not in a client. D-Bus activation then restarts Ubuntu's unit, which has no password. Unlock again. The crash signature in the journal is:

  ```
  GLib-GIO:ERROR:../../../gio/gdbusconnection.c:4749:invoke_get_property_in_idle_cb:
    assertion failed: (error != NULL)
  ```

- A D-Bus property read causes the abort, so never put a `busctl get-property … Locked` check in a watch loop; check once after each unlock.
