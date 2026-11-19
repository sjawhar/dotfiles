---
name: dotfiles-conventions
description: "Use when you create, move or wire a file in ~/.dotfiles (a shim, script, installer, tool pin, config link, skill or plugin) from a session whose working directory is elsewhere, when you upgrade a shared tool, or when you change a tool Sami operates himself (his skills, omp, dispatch, knives)."
---

# Dotfiles conventions

`~/.dotfiles` is the live checkout: every session and systemd user unit on the machine runs its files, so your edit is live for all of them when you save it. A session that works elsewhere does not have its guides loaded.

1. Read `~/.dotfiles/AGENTS.md` whole, then the `AGENTS.md` of each directory you will change. They hold placement, portability, the launcher environment, publishing, the leak-term check and the tests.
2. Publish only when the task is about these files: a change Sami asked for, or a role that owns them. Then commit only your own paths and publish as the root guide's "Publishing a change" says.
3. Leave an edit made on the way to other work uncommitted in the working copy, and name each file in your report.
4. When Sami says he is editing the checkout, leave all your edits uncommitted and name them.
5. When you change a tool Sami operates himself (his skills, dotfiles, omp, dispatch, knives), tell him what each change will do, then what it did: he uses it while you change it. Upgrade a shared tool without asking, then say what changed and how to roll it back; ask first when running sessions must restart for it (bun, node, omp).
6. A fork release is tagged `v<upstream>-sami.<YYYYMMDD>-<HHMMSS>` and titled `sami build <tag>` by the fork's release workflow, not by hand; consumers pin `latest`.
