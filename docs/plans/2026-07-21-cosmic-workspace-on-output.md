# cosmic-comp: direct monitor/workspace navigation (AeroSpace-style)

Handoff doc — self-contained; assumes no prior session context. Written 2026-07-21.

## Goal

Sami runs a 6-monitor COSMIC desktop and navigates keyboard-only. COSMIC can only
focus outputs *directionally* (`SwitchOutput(Left/...)`), which is unpredictable on a
2D grid. Wanted, in priority order:

1. **Direct monitor focus**: one chord (e.g. `Super+Alt+1..6`) focuses a *specific*
   monitor — active workspace shown there, keyboard focus + pointer move there.
2. **AeroSpace-style global workspaces** (stretch): globally-numbered workspaces
   statically pinned to monitors (1-3 → monitor A, 4-6 → monitor B, ...); one chord
   jumps to workspace N *wherever it lives*.

The deliverable is a **patched cosmic-comp** (+ its config crate), run as a local
fork via the established fork playbook, and an **upstream PR** against the
ready-made design in [cosmic-comp#2120](https://github.com/pop-os/cosmic-comp/issues/2120)
("Option B"). Related open asks: cosmic-epoch#2270, cosmic-comp#697.

Do NOT build a ydotool/keystroke hack. One existed (`laptop/aerospace-ws`) and was
retired as unreliable — see Cleanup below.

## Research findings (verified against source, July 2026)

Citations pinned at `pop-os/cosmic-comp` @ `4d370cdf` and
`pop-os/cosmic-settings-daemon` @ `e37160f1`.

- The shortcuts `Action` enum lives in **`pop-os/cosmic-settings-daemon`**, crate
  `cosmic-settings-config`, `config/src/shortcuts/action.rs` (cosmic-comp consumes it
  via `src/config/key_bindings.rs`). Every output-addressing variant is
  directional-only. **No variant addresses an output by name/index. Config-only is
  impossible.**
- `Action::Workspace(u8)` hard-codes the focused output
  (`cosmic-comp/src/input/actions.rs#L180-L192`).
- **The complete focus-transfer recipe exists in exactly one place** — the
  `Action::SwitchOutput` handler (`src/input/actions.rs#L523-L585`):
  `shell.activate(...)` + `seat.set_active_output(&output)` +
  `Shell::set_focus(...)` + pointer warp. Copy this pattern.
- cosmic-comp implements `ext-workspace-v1`; its `Activate` handler
  (`src/wayland/handlers/workspace.rs#L24-L42`) already works **cross-output** but
  has a literal `// TODO: move cursor?` — it never transfers seat/keyboard focus.
  Fixing that TODO with the same recipe is an in-scope side quest that benefits all
  external workspace clients.
- `PinnedWorkspace` (`cosmic-comp-config/src/workspace.rs#L61-L67`) already persists
  workspace→output binding by `OutputMatch{name, edid}` — the pinning primitive for
  goal 2 exists internally; it lacks a user-facing config/keybinding path.
- Neither `WorkspaceMode::Global` nor `OutputBound` matches AeroSpace semantics;
  goal 2 in full would be a third mode (issue #2120 "Option C") — treat as stretch,
  not required for goal 1.

## Design (goal 1, the PR)

1. **New action variants** in `cosmic-settings-config`'s `Action` enum
   (fork `pop-os/cosmic-settings-daemon`):
   - `SwitchToOutput(String)` — focus output by connector name or model substring.
   - `WorkspaceOnOutput(String, u8)` — activate workspace N on named output and
     focus it (covers goal 2's per-chord behavior without a new workspace mode).
   Match upstream naming taste; #2120 calls this family "Option B". Keep the
   deprecated-variant conventions of that file.
2. **Handlers** in `cosmic-comp/src/input/actions.rs`: resolve the output
   (by connector name first, fall back to EDID model substring — connector names
   churn across replugs on this machine), then perform the full `SwitchOutput`
   focus-transfer recipe. Reuse/extract a helper from the existing handler rather
   than duplicating 60 lines.
3. **Fix the `ext-workspace-v1 Activate` TODO** with the same helper.
4. **Keybindings** (local config, not upstream): `Super+Alt+1..6` →
   `SwitchToOutput(...)` for the six monitors. Note: `Super+Alt+1..9` are currently
   bound to `Workspace(N)` in `laptop/shortcuts-custom` — those bindings are
   superseded; rework that file as part of deployment.

cosmic-comp pins `cosmic-settings-config` by git rev in `Cargo.toml`/`Cargo.lock` —
point it at the fork branch (same maneuver as pointing cosmic-comp at a Smithay
fork; routine).

## Fork/build/deploy playbook (established precedent: `laptop/cosmic-greeter-fork.sh`)

- Patch against the **installed rev** (`cosmic-comp --version` prints the git sha;
  the repo tags builds as `0.1~<epoch>~24.04~<sha>`), then rebase onto master for
  the PR.
- Toolchain: repo needs rustc newer than the mise-pinned default — use
  `mise install rust@1.93.0` + `mise x rust@1.93.0 -- cargo build --release`
  (already installed on the laptop). Build deps: see `debian/control`
  (libinput-dev, libpam0g-dev(? greeter only), libwayland-dev, libxkbcommon-dev,
  libclang-dev, pkg-config are already installed from prior builds).
- **Install with `sudo install`, never `cp`** — `cp` onto a running binary fails
  with `Text file busy`, and it once failed *silently* here. **Verify by sha256
  comparing the built artifact to `/usr/bin/cosmic-comp` post-install.** Never
  verify by grepping terminal scrollback for echoed marker strings.
- Back up stock binary to `/usr/bin/cosmic-comp.stock` first. Restart path: killing
  cosmic-comp ends the session BUT cosmic-session respawns it (verified 2026-07-20);
  apps die, tmux/ssh survive. Schedule the swap with the user, don't surprise them.
- `sudo apt-mark hold cosmic-comp` after install; add
  `laptop/cosmic-comp-fork.sh` mirroring `laptop/cosmic-greeter-fork.sh`
  (hold + dpkg-verify fork detection + rollback + post-merge cleanup notes), wire
  into `laptop/install.sh`.
- Fork remotes: push branch to `sjawhar/cosmic-comp` (and `sjawhar/cosmic-settings-daemon`).
  PR upstream referencing #2120 (and note it fixes the workspace-handler TODO).
  Precedent PR style: pop-os/cosmic-greeter#493.

## Testing protocol (real usage, not just compile)

1. Bind the new actions in COSMIC custom shortcuts
   (`~/.config/cosmic/com.system76.CosmicSettings.Shortcuts/v1/custom` — deployed
   from `laptop/shortcuts-custom` by `laptop/install.sh` via `__HOME__` sed; it is
   NOT a symlink).
2. From each monitor, chord to every other monitor: verify the target workspace
   becomes visible, **keyboard focus lands there (type into a terminal on the
   target), and the pointer warped**.
3. Include both DisplayLink outputs (`DVI-I-*`, evdi) — they're the slow/weird path.
4. Verify directional `SwitchOutput` and plain `Workspace(N)` still work (no
   regression), and that cosmic-settings' shortcut UI doesn't crash rendering the
   unknown-to-it custom action.
5. Connector names churn (DP-12→DP-16, DVI-I-2→DVI-I-3 observed within one day) —
   test the model-substring fallback by replugging the ASUS.

## Environment facts

- Machine: Pop!_OS 24.04, COSMIC, `jj` for VCS (never git commands; colocated repo).
- 6 outputs (names WILL drift; models are stable): eDP-1 laptop 1920x1200 · Samsung
  LS32A70 4K portrait rotate90 (left) · Samsung U32J59x 4K portrait rotate270
  (right) · Sceptre O34 3440x1440 · AOC U34G2G4R3 3440x1440 · ASUS MB16AC 1080p
  rotate180 (DisplayLink/evdi, as is one of the ultrawides — whichever is on the
  Plugable USBC-6950M). Canonical layout + model-matching connector lookup:
  `scripts/fix-monitors`.
- **Known unrelated bug — do not poke it**: disabling an evdi output via
  cosmic-settings deadlocked the compositor (2026-07-20). Evidence bundle:
  `~/cosmic-comp-freeze-2026-07-20/`. Filing that upstream is a separate task; just
  don't use settings-disable on DisplayLink outputs while testing.
- The lock screen runs a patched cosmic-greeter fork (PR #493, package held) —
  unrelated, don't "fix" the hold.

## Cleanup (in scope)

Retire the dead ydotool hack: delete `laptop/aerospace-ws`,
`laptop/aerospace-ws.conf`, their `ensure_link`/chmod lines in `laptop/install.sh`,
the `Alt+1..9` Spawn bindings in `laptop/shortcuts-custom`, and
`~/.local/bin/aerospace-ws`. The user confirmed it never worked reliably.

## Standing approvals (from the 2026-07-21 session)

- Building this feature, forking, holding the package, and opening the upstream PR:
  **approved**.
- Commit/push to the dotfiles repo: allowed for this work, one commit per logical
  unit, **short commit messages**; history rewrites only on request.
- sudo: run commands in the user's tmux pane (`dev` session — VERIFY the pane is a
  plain shell immediately before every send; panes get reused by opencode TUIs) or
  ask the user; sudo prompts = YubiKey touch, tell the user when to touch.
- Compositor restart for the binary swap: coordinate timing with the user first.
