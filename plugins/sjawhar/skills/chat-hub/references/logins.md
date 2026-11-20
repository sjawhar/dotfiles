# Repairing a chat hub login

Run every command here on oryx. `chathub/login` takes the network names in its `BOTS`; its usage, `--keep` and exit codes are in its header.

The Google Messages login of the agents' phone needs no step by Sami: an agent pairs it again by section "Logins" of `skills/majordomo/references/sites/chat-hub.md` in the private repository `sjawhar/dojo`. Every other login signs in again with Sami, as below.

## A down login

A down login on Google Messages, WhatsApp, Signal, Messenger, Instagram or LinkedIn signs in again with `relogin` in that bridge's control room. Send each command with `secrets MATRIX_HUB_URL MATRIX_HUB_TOKEN -- ~/.dotfiles/chathub/login <network> <command>`.

1. Send `list-logins`. The reply lists the login IDs.
2. Send `relogin <login ID>`. If the reply asks for a flow, send `relogin <login ID> <flow ID>`.
3. Continue by network:
   - WhatsApp and Signal: use the flow `qr` and follow the QR procedure.
   - Google Messages, Messenger, Instagram and LinkedIn: the bot replies with the step it needs, for example cookies from a signed-in browser. Show Sami each step. Send a cookie or token from Sami with `chathub/login <network> -`, which reads it from stdin and does not print it.
   - Discord: send `login-qr` only when Discord has no login, and follow the QR procedure for it. Send `reconnect` (`chathub/login discord reconnect`) only when its login has no connection.

Do not use `login` to repair a down Google Messages (SMS) login. A new `login` creates a second login with new rooms, and the old rooms can no longer send. The agents' phone is a second login on purpose, because it has its own number; a new login for the same number is not a repair.

## QR procedure

1. Run `~/.dotfiles/chathub/login-screen`. Leave it running.
2. Start the QR login. WhatsApp or Signal: `secrets MATRIX_HUB_URL MATRIX_HUB_TOKEN -- ~/.dotfiles/chathub/login [--keep MINUTES] <network> relogin <login ID> qr`. Discord: `secrets MATRIX_HUB_URL MATRIX_HUB_TOKEN -- ~/.dotfiles/chathub/login [--keep MINUTES] discord login-qr`.
3. Send Sami `http://localhost:6080/`.
4. Run one `chathub/login` command at a time, and wait for exit 0 (linked).
5. Stop the screen after Sami finishes.

## Website procedure (only for a network with no login)

mautrix-manager makes a new login; it cannot sign in an existing login again. Use this only for a network that has no login of that account, not for a down login.

1. Run `~/.dotfiles/chathub/login-screen`. Leave it running.
2. Send Sami each `Window "mautrix-manager":` DevTools link it prints; he signs in through the DevTools screencast.
3. Stop the screen after Sami finishes.

The header of `chathub/login-screen` covers the manager's first sign-in to the hub and the 30-minute limit on a sign-in; "Logins" in `docs/plans/2026-10-03-chat-hub-design.md` covers the Google Messages sign-in.
