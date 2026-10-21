---
name: unlock-keyring
description: "Use when the devbox GNOME keyring is locked, or after a devbox reboot. Symptoms: `keyring.errors.KeyringLocked: Failed to unlock the collection!`, `hawk login` crashing at token storage after a successful login, or any Python `keyring` client failing to read or write a secret."
---

# Unlock the devbox keyring

Every reboot locks the `login` keyring. SSH login cannot unlock it: `ubuntu` has no Unix password, and only `common-password` loads `pam_gnome_keyring.so`.

## Unlock

1. Only Sami has the password. Ask him, and let him type it at the prompt.
2. Run:

   ```bash
   systemctl --user stop gnome-keyring-daemon.service gnome-keyring-daemon.socket
   read -rsp 'Keyring password: ' KP && printf '%s' "$KP" | \
     gnome-keyring-daemon --unlock --components=pkcs11,secrets --daemonize; unset KP; echo
   busctl --user get-property org.freedesktop.secrets \
     /org/freedesktop/secrets/collection/login \
     org.freedesktop.Secret.Collection Locked   # must print: b false
   ```

- Never automate the password. Never bypass the lock with `PYTHON_KEYRING_BACKEND` or a plaintext backend.
- Always stop the stock units first. `--unlock` unlocks only a daemon that it starts. While the stock service runs, each attempt logs `another secret service is running` and changes nothing. `--start` is incompatible with `--unlock`.
- Never retry with a bare `gnome-keyring-daemon --unlock`. Each one takes `/run/user/1000/keyring/control` and deletes the control directory when it exits, so clients fail with `couldn't access control socket: No such file or directory`. To recover:
  1. Kill each stray `gnome-keyring-daemon --unlock` by PID.
  2. `systemctl --user restart gnome-keyring-daemon.socket`
  3. Do the unlock again.

## Relocks (approximately daily)

- A relock is a SIGABRT crash of the unlocked daemon. D-Bus activation then starts the stock service, which has no password. Unlock again.
- The bug is in the Ubuntu `gnome-keyring` package (46.1-2ubuntu0.2), not in a client. Signature:

  ```
  GLib-GIO:ERROR:../../../gio/gdbusconnection.c:4749:invoke_get_property_in_idle_cb:
    assertion failed: (error != NULL)
  ```

- A D-Bus property read causes the abort. Never put the `busctl get-property … Locked` check in a watch loop. Check one time after each unlock.
- To read a crash report:
  1. apport keeps one report per binary. Move `/var/crash/_usr_bin_gnome-keyring-daemon.1000.crash` aside, if it exists, before you expect a new report.
  2. `apport-unpack <file> <dir>`
  3. `gdb -q -batch -ex 'p (char *) __glib_assert_msg' -ex 'bt 12' /usr/bin/gnome-keyring-daemon <dir>/CoreDump`
