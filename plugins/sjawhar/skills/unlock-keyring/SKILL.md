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
