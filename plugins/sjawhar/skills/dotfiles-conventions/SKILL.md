---
name: dotfiles-conventions
description: "Use when you create, move or wire a file in ~/.dotfiles (a shim, script, installer, tool pin, config link, skill or plugin), or upgrade a tool it pins, from a session whose working directory is elsewhere."
---

# Dotfiles conventions

`~/.dotfiles` is the live checkout: every session and systemd user unit on the machine runs its files, so your edit is live for all of them when you save it. A session that works elsewhere does not have its guides loaded.

1. Read `~/.dotfiles/AGENTS.md` whole, then the `AGENTS.md` of each directory you will change. They hold placement, portability, the launcher environment, publishing, the leak-term check and the tests.
2. Publish only when the task is about these files: a change Sami asked for, or a role that owns them. Then commit only your own paths and publish as the root guide's "Publishing a change" says.
3. Leave an edit made on the way to other work uncommitted in the working copy, and name each file in your report.
4. When Sami says he is editing the checkout, leave all your edits uncommitted and name them.
