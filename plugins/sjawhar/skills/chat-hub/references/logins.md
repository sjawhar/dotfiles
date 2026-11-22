# Repairing a chat hub login

Run every command here on oryx. `chathub/login` takes the network names in its `BOTS`. Its usage, `--keep` and exit codes are in its header.

The Google Messages login of the agents' phone needs no step by Sami: an agent pairs it again by section "Logins" of `skills/majordomo/references/sites/chat-hub.md` in the private repository `sjawhar/dojo`. Every other login signs in again with Sami, as below.

## A down login

A down login on Google Messages, WhatsApp, Signal, Messenger, Instagram or LinkedIn signs in again with `relogin` in that bridge's control room. Send each command with `secrets MATRIX_HUB_URL MATRIX_HUB_TOKEN -- ~/.dotfiles/chathub/login <network> <command>`.

1. **Send `list-logins`.** The reply lists the login IDs.
2. **Send `relogin <login ID>`.**
   - Send `relogin <login ID> <flow ID>` if the reply asks for a flow.
3. **Continue by network:**
   - **WhatsApp and Signal:**
     - Use the flow `qr`.
     - Follow the QR procedure.
   - **Google Messages, Messenger, Instagram and LinkedIn:** the bot replies with the step it needs, for example cookies from a signed-in browser.
     - Show Sami each step.
     - Send a cookie or token from Sami with `chathub/login <network> -`, which reads it from stdin and does not print it.
   - **Discord:**
     - Send `login-qr` only when Discord has no login.
     - Follow the QR procedure for `login-qr`.
     - Send `reconnect` (`chathub/login discord reconnect`) only when its login has no connection.

Do not use `login` to repair a down Google Messages (SMS) login. A new `login` creates a second login with new rooms. The old rooms can no longer send. The agents' phone is a second login on purpose, because it has its own number. A new login for the same number is not a repair.

## QR procedure

1. **Run `~/.dotfiles/chathub/login-screen`.** Leave it running.
2. **Start the QR login** with `secrets MATRIX_HUB_URL MATRIX_HUB_TOKEN -- ~/.dotfiles/chathub/login [--keep MINUTES] <arguments>`:

   | Network | `<arguments>` |
   | --- | --- |
   | WhatsApp or Signal | `<network> relogin <login ID> qr` |
   | Discord | `discord login-qr` |

3. **Send Sami `http://localhost:6080/`.**
4. **Run one `chathub/login` command at a time.** Wait for exit 0 (linked).
5. **Stop the screen** after Sami finishes.

## Website procedure (only for a network with no login)

Use this only for a network that has no login of that account, not for a down login: mautrix-manager makes a new login. It cannot sign in an existing login again.

1. **Run `~/.dotfiles/chathub/login-screen`.** Leave it running.
2. **Send Sami each `Window "mautrix-manager":` DevTools link it prints.** He signs in through the DevTools screencast.
3. **Stop the screen** after Sami finishes.

The header of `chathub/login-screen` covers the manager's first sign-in to the hub and the 30-minute limit on a sign-in. "Logins" in `docs/plans/2026-10-03-chat-hub-design.md` covers the Google Messages sign-in.
