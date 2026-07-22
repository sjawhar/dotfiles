# cosmic-comp Direct Monitor Focus (`SwitchToOutput` / `WorkspaceOnOutput`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> Spec: [docs/plans/2026-07-21-cosmic-workspace-on-output.md](2026-07-21-cosmic-workspace-on-output.md) — read it first; it holds environment facts, standing approvals, and the testing protocol.

**Goal:** One keyboard chord focuses a specific monitor (and optionally a specific workspace on it) on Sami's 6-monitor COSMIC desktop, via two new shortcut actions in a patched cosmic-comp, deployed locally and PR'd upstream against [cosmic-comp#2120](https://github.com/pop-os/cosmic-comp/issues/2120) ("Option B").

**Architecture:** Add `SwitchToOutput(String)` and `WorkspaceOnOutput(String, u8)` variants to the `Action` enum in `cosmic-settings-config` (lives in the `pop-os/cosmic-settings-daemon` repo). In cosmic-comp, extract the existing `SwitchOutput` focus-transfer recipe (`shell.activate` + `seat.set_active_output` + `Shell::set_focus` + pointer warp) into a reusable `State::switch_to_output` helper; add handlers for the new actions that resolve outputs by connector name with EDID-model-substring fallback; reuse the helper to fix the `ext-workspace-v1 Activate` handler's `// TODO: move cursor?`.

**Tech Stack:** Rust 1.93 (via `mise x rust@1.93.0`), smithay, jj (colocated repos in `~/Code/cosmic-comp` and `~/Code/cosmic-settings-daemon`), gh CLI, RON config.

## Global Constraints

- **jj only, never git commands** (repos in `~/Code` are already jj-colocated; the one allowed exception already happened: `git fetch --unshallow`).
- Patch against installed rev: cosmic-comp `7ee78d9c1369033aa4f963e22d2864bbcda1fbff`, cosmic-settings-config `e37160f14d1e7ee428f973cd2848b4e95f83dfe1` (both repos already have `@` parked on these revs).
- Build with `mise x rust@1.93.0 -- cargo ...` (repo requires rust 1.93; mise-pinned default is 1.92; 1.93.0 already installed).
- **Install with `sudo install`, never `cp`** onto the running binary; verify by comparing `sha256sum` of built artifact vs `/usr/bin/cosmic-comp`. Never verify via terminal-scrollback grep.
- sudo runs happen in the user's tmux pane (`dev` session — verify the pane is a plain shell immediately before every send) or by asking the user; sudo prompt = YubiKey touch, tell the user when to touch.
- Compositor restart kills apps (tmux/ssh survive) — **coordinate timing with the user first**.
- Do NOT disable DisplayLink/evdi outputs via cosmic-settings while testing (known deadlock, see spec).
- Dotfiles: one commit per logical unit, short commit messages, push to main allowed.
- Fork remotes: `sjawhar/cosmic-comp`, `sjawhar/cosmic-settings-daemon`. PR precedent: pop-os/cosmic-greeter#493.

## Verification Model (honest TDD boundaries)

Unit-testable: RON serde round-trip of new `Action` variants (Task 2). Everything else is compositor behavior with no test harness — verification is `cargo check`/`cargo build` plus the live testing protocol (Task 10) run with the user at the keyboard. Refactor safety (Task 4) is covered by the regression items in Task 10.

---

### Task 1: GitHub forks + remotes

**Files:** none (repo setup)

**Interfaces:**
- Produces: jj remote `sjawhar` in both `~/Code/cosmic-comp` and `~/Code/cosmic-settings-daemon`; empty GitHub forks.

- [ ] **Step 1: Create forks (no clones — we have them)**

```bash
gh repo fork pop-os/cosmic-comp --clone=false
gh repo fork pop-os/cosmic-settings-daemon --clone=false
```
Expected: "Created fork sjawhar/..." (or "already exists").

- [ ] **Step 2: Add remotes**

```bash
cd ~/Code/cosmic-comp && jj git remote add sjawhar https://github.com/sjawhar/cosmic-comp
cd ~/Code/cosmic-settings-daemon && jj git remote add sjawhar https://github.com/sjawhar/cosmic-settings-daemon
```
Expected: no output. Verify with `jj git remote list` showing `origin` + `sjawhar`.

---

### Task 2: New `Action` variants in cosmic-settings-config

**Files:**
- Modify: `~/Code/cosmic-settings-daemon/config/src/shortcuts/action.rs`
- Test: same file (`#[cfg(test)] mod tests` at bottom)

**Interfaces:**
- Produces: `Action::SwitchToOutput(String)` and `Action::WorkspaceOnOutput(String, u8)` — consumed by cosmic-comp in Tasks 5.

Working copy is already parked on `e37160f1` (`jj log -r @` to confirm).

- [ ] **Step 1: Add variants**

In `config/src/shortcuts/action.rs`, after the `SwitchOutput(Direction)` variant (line ~117), add:

```rust
    /// Move to the output matching the given connector name or EDID model substring
    SwitchToOutput(String),
```

After the `Workspace(u8)` variant (line ~144), add:

```rust
    /// Change focus to the given workspace ID on the output matching the given
    /// connector name or EDID model substring
    WorkspaceOnOutput(String, u8),
```

- [ ] **Step 2: Write the serde round-trip test**

`ron` is already a regular dependency of the crate. At the bottom of `action.rs`:

```rust
#[cfg(test)]
mod tests {
    use super::Action;

    #[test]
    fn output_addressed_actions_ron_roundtrip() {
        let cases = [
            (
                r#"SwitchToOutput("DP-1")"#,
                Action::SwitchToOutput("DP-1".into()),
            ),
            (
                r#"WorkspaceOnOutput("LS32A70", 2)"#,
                Action::WorkspaceOnOutput("LS32A70".into(), 2),
            ),
        ];
        for (text, action) in cases {
            assert_eq!(ron::from_str::<Action>(text).unwrap(), action);
            let serialized = ron::to_string(&action).unwrap();
            assert_eq!(ron::from_str::<Action>(&serialized).unwrap(), action);
        }
    }
}
```

- [ ] **Step 3: Run the test**

```bash
cd ~/Code/cosmic-settings-daemon && mise x rust@1.93.0 -- cargo test -p cosmic-settings-config
```
Expected: PASS (including existing `binding_from_str` tests).

- [ ] **Step 4: Commit and push the fork branch**

```bash
jj describe -m "shortcuts: add SwitchToOutput and WorkspaceOnOutput actions"
jj bookmark set output-addressed-actions
jj git push --remote sjawhar --bookmark output-addressed-actions --allow-new
jj new
```
Expected: bookmark pushed to `sjawhar/cosmic-settings-daemon`.

---

### Task 3: Point cosmic-comp at the patched config crate

**Files:**
- Modify: `~/Code/cosmic-comp/Cargo.toml:27` + `Cargo.lock`

**Interfaces:**
- Produces: cosmic-comp builds against the fork's `cosmic-settings-config`. The deploy branch commits the **git fork** dependency (reproducible anywhere); a local `[patch]` path override is allowed only as an uncommitted dev convenience while iterating.

- [ ] **Step 1: Repoint the dependency**

In `~/Code/cosmic-comp/Cargo.toml` line 27, change:

```toml
cosmic-settings-config = { git = "https://github.com/pop-os/cosmic-settings-daemon" }
```
to:
```toml
cosmic-settings-config = { git = "https://github.com/sjawhar/cosmic-settings-daemon", branch = "output-addressed-actions" }
```

- [ ] **Step 2: Update the lockfile and baseline-check**

```bash
cd ~/Code/cosmic-comp
mise x rust@1.93.0 -- cargo update -p cosmic-settings-config
mise x rust@1.93.0 -- cargo check
```
Expected: lock re-pins to the fork rev; `cargo check` completes with warnings at most (pre-code-change baseline).

---

### Task 4: Extract the focus-transfer helper (`State::switch_to_output`)

**Files:**
- Modify: `~/Code/cosmic-comp/src/input/actions.rs` (`Action::SwitchOutput` arm, lines 523–587, and new method in `impl State`)

**Interfaces:**
- Consumes: nothing new.
- Produces: `pub(crate) fn switch_to_output(&mut self, target: &Output, workspace_idx: Option<usize>, seat: &Seat<State>, serial: Serial, time: u32)` on `State` — used by Tasks 5 and 6. `workspace_idx: None` keeps the target output's currently-active workspace.

- [ ] **Step 1: Add the helper method**

Inside the existing `impl State` block in `src/input/actions.rs` (after `handle_shortcut_action`), add — this is the body currently inlined in the `SwitchOutput` arm, minus the propagate/previous-workspace block, with `next_output` generalized to `target` and the workspace index parameterized.

**First**, extend the smithay import at the top of the file — `Output` is NOT re-exported by `crate::utils::prelude` (it only privately uses it):

```rust
use smithay::{
    input::{Seat, pointer::MotionEvent},
    output::Output,
    utils::{Point, Serial},
};
```

Then add the method:

```rust
    /// Activate `workspace_idx` (or the currently-active workspace) on `target`
    /// and transfer keyboard focus + pointer there.
    pub(crate) fn switch_to_output(
        &mut self,
        target: &Output,
        workspace_idx: Option<usize>,
        seat: &Seat<State>,
        serial: Serial,
        time: u32,
    ) {
        let mut shell = self.common.shell.write();
        let res = {
            let mut workspace_guard = self.common.workspace_state.update();
            let idx = workspace_idx.unwrap_or_else(|| shell.workspaces.active_num(target).1);
            let res = shell.activate(
                target,
                idx,
                WorkspaceDelta::new_shortcut(),
                &mut workspace_guard,
            );
            seat.set_active_output(target);
            res
        };

        if let Ok(new_pos) = res {
            let workspace = shell.workspaces.active(target).unwrap().1;
            let new_target = workspace
                .focus_stack
                .get(seat)
                .last()
                .cloned()
                .map(Into::<KeyboardFocusTarget>::into);
            std::mem::drop(shell);

            let update_cursor = self.common.config.cosmic_conf.cursor_follows_focus;
            Shell::set_focus(self, new_target.as_ref(), seat, None, update_cursor);

            if let Some(ptr) = seat.get_pointer() {
                // Update cursor position if `set_focus` didn't already
                if !update_cursor {
                    ptr.motion(
                        self,
                        None,
                        &MotionEvent {
                            location: new_pos.to_f64().as_logical(),
                            serial,
                            time,
                        },
                    );
                }
                ptr.frame(self);
            }
        }
    }
```

- [ ] **Step 2: Rewrite the `SwitchOutput` arm to use it**

Replace the entire `Action::SwitchOutput(direction) => { ... }` arm (lines 523–587) with:

```rust
            Action::SwitchOutput(direction) => {
                let current_output = seat.active_output();

                let next_output = self
                    .common
                    .shell
                    .read()
                    .next_output(&current_output, direction)
                    .cloned();

                if let Some(next_output) = next_output {
                    if propagate {
                        let mut shell = self.common.shell.write();
                        if let Some((serial, prev_output, prev_idx)) =
                            shell.previous_workspace_idx.take()
                            && seat.last_modifier_change().is_some_and(|s| s == serial)
                            && prev_output == current_output
                        {
                            let _ = shell.activate(
                                &current_output,
                                prev_idx,
                                WorkspaceDelta::new_shortcut(),
                                &mut self.common.workspace_state.update(),
                            );
                        }
                    }

                    self.switch_to_output(&next_output, None, seat, serial, time);
                }
            }
```

Note the `serial` inside the inner `if let` intentionally shadows the parameter only within that block (same as upstream's code); the helper call uses the outer `serial`.

- [ ] **Step 3: Compile**

```bash
cd ~/Code/cosmic-comp && mise x rust@1.93.0 -- cargo check
```
Expected: success, no new warnings. Behavior regression is checked live in Task 10 (directional `Ctrl+Alt+arrows` still work).

---

### Task 5: Handlers for the new actions

**Files:**
- Modify: `~/Code/cosmic-comp/src/input/actions.rs` (new match arms after `SwitchOutput`; new free function near `propagate_by_default`)

**Interfaces:**
- Consumes: `Action::SwitchToOutput` / `Action::WorkspaceOnOutput` (Task 2), `State::switch_to_output` (Task 4).
- Produces: `fn output_for_query<'a>(outputs: impl Iterator<Item = &'a Output>, query: &str) -> Option<Output>` — also conceptually reused by docs/tests; connector-name exact match wins, else first case-insensitive EDID-model substring match. (Connector names churn across replugs on this machine — DP-12→DP-16 observed — so model matching is the durable path; same approach as `scripts/fix-monitors`.)

- [ ] **Step 1: Add the output resolution function**

Below `propagate_by_default` in `src/input/actions.rs`:

```rust
/// Resolve an output by exact connector name (e.g. "DP-1"), falling back to a
/// case-insensitive substring match on the EDID model (e.g. "LS32A70").
fn output_for_query<'a>(
    outputs: impl Iterator<Item = &'a Output>,
    query: &str,
) -> Option<Output> {
    let query_lower = query.to_lowercase();
    let mut model_match = None;
    for output in outputs {
        if output.name() == query {
            return Some(output.clone());
        }
        if model_match.is_none()
            && output
                .physical_properties()
                .model
                .to_lowercase()
                .contains(&query_lower)
        {
            model_match = Some(output.clone());
        }
    }
    model_match
}
```

(`Output` is imported explicitly in Task 4's import block — the prelude does NOT re-export it. Extension methods like `output.name()` come via `crate::utils::prelude::*`.)

- [ ] **Step 2: Add the match arms**

Immediately after the rewritten `Action::SwitchOutput` arm:

```rust
            Action::SwitchToOutput(query) => {
                let target_output = {
                    let shell = self.common.shell.read();
                    output_for_query(shell.outputs(), &query)
                };

                if let Some(output) = target_output {
                    self.switch_to_output(&output, None, seat, serial, time);
                } else {
                    warn!("SwitchToOutput: no output matches {query:?}");
                }
            }

            Action::WorkspaceOnOutput(query, key_num) => {
                let target_output = {
                    let shell = self.common.shell.read();
                    output_for_query(shell.outputs(), &query)
                };
                let workspace = match key_num {
                    0 => 9,
                    x => x - 1,
                };

                if let Some(output) = target_output {
                    self.switch_to_output(&output, Some(workspace as usize), seat, serial, time);
                } else {
                    warn!("WorkspaceOnOutput: no output matches {query:?}");
                }
            }
```

(`key_num` mapping `0 => 9, x => x - 1` matches the existing `Action::Workspace` arm. If the index exceeds the output's workspace count, `shell.activate` returns `Err(InvalidWorkspaceIndex)` and nothing happens — same failure mode as `Workspace(n)`.)

- [ ] **Step 3: Compile**

```bash
cd ~/Code/cosmic-comp && mise x rust@1.93.0 -- cargo check
```
Expected: success. (If the match is non-exhaustive elsewhere — e.g. cosmic-settings-like rendering code inside cosmic-comp — the compiler will point at every site that needs a new arm; add sensible arms there too.)

---

### Task 6: Fix the `ext-workspace-v1 Activate` focus TODO

**Files:**
- Modify: `~/Code/cosmic-comp/src/wayland/handlers/workspace.rs:24-42`

**Interfaces:**
- Consumes: `State::switch_to_output` (Task 4).

- [ ] **Step 1: Rewrite the `Request::Activate` arm**

Replace lines 24–42 with:

```rust
                Request::Activate(handle) => {
                    let maybe = {
                        let shell = self.common.shell.read();
                        shell.workspaces.iter().find_map(|(o, set)| {
                            set.workspaces
                                .iter()
                                .position(|w| w.handle == handle)
                                .map(|i| (o.clone(), i))
                        })
                    };

                    if let Some((output, idx)) = maybe {
                        let seat = self.common.shell.read().seats.last_active().clone();
                        let serial = SERIAL_COUNTER.next_serial();
                        let time = self.common.clock.now().as_millis();
                        self.switch_to_output(&output, Some(idx), &seat, serial, time);
                    }
                }
```

Update imports at the top of the file: add `SERIAL_COUNTER` (`use smithay::{reexports::wayland_server::DisplayHandle, utils::SERIAL_COUNTER};`) and remove `crate::shell::WorkspaceDelta` if now unused (compiler will say).

- [ ] **Step 2: Compile clean**

```bash
cd ~/Code/cosmic-comp && mise x rust@1.93.0 -- cargo check
```
Expected: success, no unused-import warnings.

- [ ] **Step 3: Commit the cosmic-comp branch**

```bash
cd ~/Code/cosmic-comp
jj describe -m "shortcuts: add SwitchToOutput/WorkspaceOnOutput, focus on ext-workspace activate"
jj bookmark set switch-to-output
jj new
```
(Push to `sjawhar` happens in Task 11 after live testing proves the design.)

---

### Task 7: Release build

- [ ] **Step 1: Build**

```bash
cd ~/Code/cosmic-comp && mise x rust@1.93.0 -- cargo build --release
```
Expected: `Finished` line; binary at `target/release/cosmic-comp`. Build deps (libinput-dev, libwayland-dev, libxkbcommon-dev, libclang-dev, pkg-config) are already installed from prior builds.

- [ ] **Step 2: Record the hash**

```bash
sha256sum ~/Code/cosmic-comp/target/release/cosmic-comp
```
Save this value — Task 8 compares against it.

---

### Task 8: Deploy (COORDINATE WITH USER)

All sudo here = user's tmux pane or user-run; YubiKey touch per command. **Ask the user when to do the compositor restart before starting.**

- [ ] **Step 1: Back up the stock binary (fail-closed, idempotent)**

Refuse to overwrite an existing backup, and refuse to "back up" an already-modified binary:

```bash
if sudo test -e /usr/bin/cosmic-comp.stock; then
  echo "stock backup already exists"
elif dpkg -V cosmic-comp 2>/dev/null | grep -q '/usr/bin/cosmic-comp$'; then
  echo "current cosmic-comp is modified; refusing to overwrite stock backup" && exit 1
else
  sudo install -m 755 /usr/bin/cosmic-comp /usr/bin/cosmic-comp.stock
fi
```
(Reading the running binary is safe; only in-place *writes* are forbidden.) Verify: `ls -la /usr/bin/cosmic-comp.stock`.

- [ ] **Step 2: Install the fork**

```bash
sudo install -m 755 ~/Code/cosmic-comp/target/release/cosmic-comp /usr/bin/cosmic-comp
```

- [ ] **Step 3: Verify by comparison (MANDATORY, fail-closed)**

```bash
cmp /usr/bin/cosmic-comp ~/Code/cosmic-comp/target/release/cosmic-comp && echo INSTALL-VERIFIED
sha256sum /usr/bin/cosmic-comp ~/Code/cosmic-comp/target/release/cosmic-comp
```
Expected: `INSTALL-VERIFIED` and identical hashes. If not, STOP — the install failed silently.

- [ ] **Step 4: Hold the package**

```bash
sudo apt-mark hold cosmic-comp
```

- [ ] **Step 5: Restart the compositor (user-scheduled)**

With the user's go-ahead: user logs out/in, or `pkill cosmic-comp` (cosmic-session respawns it — verified 2026-07-20; apps die, tmux/ssh survive). After restart, verify the session came back and `cosmic-comp --version` still prints a version.

**Rollback if the session won't come up** (from a TTY: Ctrl+Alt+F3):
```bash
sudo install -m 755 /usr/bin/cosmic-comp.stock /usr/bin/cosmic-comp
pkill cosmic-comp
```

---

### Task 9: Keybindings + retire the ydotool hack (dotfiles)

**Files:**
- Modify: `laptop/shortcuts-custom` (replace `Alt+1..9` Spawn block and `Super+Alt+1..9` Workspace block)
- Modify: `laptop/install.sh` (delete lines 200–202: the `--- Aerospace-style workspace switching ---` block)
- Delete: `laptop/aerospace-ws`, `laptop/aerospace-ws.conf`, `~/.local/bin/aerospace-ws` (symlink)

**Interfaces:**
- Produces: deployed `~/.config/cosmic/com.system76.CosmicSettings.Shortcuts/v1/custom` with `SwitchToOutput` bindings (NOT a symlink; regenerated via sed).

- [ ] **Step 1: Rework `laptop/shortcuts-custom`**

Remove the nine `Spawn("__HOME__/.local/bin/aerospace-ws N")` lines and the nine `(modifiers: [Super, Alt], key: "N"): Workspace(N)` lines. In their place (keep the voxtype and `Ctrl+Alt` directional blocks):

```
    (modifiers: [Super, Alt], key: "1", description: Some("Focus laptop")): SwitchToOutput("eDP-1"),
    (modifiers: [Super, Alt], key: "2", description: Some("Focus Samsung portrait left")): SwitchToOutput("LS32A70"),
    (modifiers: [Super, Alt], key: "3", description: Some("Focus AOC ultrawide")): SwitchToOutput("U34G2G4R3"),
    (modifiers: [Super, Alt], key: "4", description: Some("Focus Sceptre ultrawide")): SwitchToOutput("O34"),
    (modifiers: [Super, Alt], key: "5", description: Some("Focus Samsung portrait right")): SwitchToOutput("U32J59x"),
    (modifiers: [Super, Alt], key: "6", description: Some("Focus ASUS portable")): SwitchToOutput("MB16AC"),
```

Numbering mirrors physical left→right/top→bottom from `scripts/fix-monitors` (laptop, Samsung-left, AOC, Sceptre, Samsung-right, ASUS). Confirm the mapping with the user during testing; it's a one-line-each tweak.

- [ ] **Step 2: Delete the hack + installer lines**

Remove `laptop/aerospace-ws`, `laptop/aerospace-ws.conf`; delete the three lines in `laptop/install.sh` under `# --- Aerospace-style workspace switching ---` (chmod + ensure_link); remove the deployed symlink:

```bash
rm ~/.local/bin/aerospace-ws
```

- [ ] **Step 3: Deploy the shortcuts file**

```bash
COSMIC_SHORTCUTS=~/.config/cosmic/com.system76.CosmicSettings.Shortcuts/v1
rm -f "$COSMIC_SHORTCUTS/custom"
sed "s|__HOME__|${HOME}|g" ~/.dotfiles/laptop/shortcuts-custom > "$COSMIC_SHORTCUTS/custom"
```
Expected: COSMIC picks up shortcut config changes live (no restart needed). Verify: `grep SwitchToOutput "$COSMIC_SHORTCUTS/custom"` shows 6 lines.

---

### Task 10: Live testing protocol (WITH USER)

Real usage, not compile checks. User at the keyboard; I watch journal output (`journalctl --user -u cosmic-comp -f` or session log) for the `warn!` lines.

- [ ] **Direct focus matrix:** From each of the 6 monitors, chord `Super+Alt+1..6` to every other monitor. Verify per jump: target workspace visible, keyboard focus landed (type into a terminal on the target), pointer warped.
- [ ] **DisplayLink path:** Include both evdi outputs (`DVI-I-*`) as chord targets — they're the slow/weird path.
- [ ] **Regression — directional:** `Ctrl+Alt+Left/Right/Up/Down` still switch outputs (exercises the refactored `SwitchOutput` arm incl. propagate path).
- [ ] **Regression — workspaces:** COSMIC default `Super+1..9` `Workspace(N)` bindings still work on the focused output.
- [ ] **ext-workspace fix:** Click a workspace on a *different* monitor in the workspaces applet/overview — keyboard focus and pointer must now follow (previously only the workspace switched).
- [ ] **cosmic-settings UI:** Open Settings → Keyboard → Shortcuts; verify it doesn't crash rendering the custom actions it doesn't recognize (it has its own build of `cosmic-settings-config`). Record how it renders them. If it crashes or eats the custom file, that's a blocker to fix (fallback: keep bindings but investigate settings' parse path).
- [ ] **Model-substring fallback:** Replug the ASUS (connector name churns) and verify `Super+Alt+6` still lands on it without config changes.
- [ ] **Bad query behavior:** Temporarily bind `SwitchToOutput("NOPE")`, chord it, verify nothing happens and the warn line appears in the log; remove the binding.
- [ ] **WorkspaceOnOutput live test:** Temporarily bind e.g. `(modifiers: [Super, Alt], key: "0", description: Some("WS2 on Samsung left")): WorkspaceOnOutput("LS32A70", 2)`; from a *different* monitor, chord it and verify workspace 2 activates on that output, keyboard focus lands there, and the pointer warps. Decide with the user whether to keep permanent bindings; otherwise remove.

Fix-and-rebuild loop for any defect: edit → `cargo build --release` → Task 8 steps 2–3 (install + hash) → restart (user-scheduled) → re-test. Do NOT use cosmic-settings to disable evdi outputs (deadlock bug).

---

### Task 11: Fork tracking script + dotfiles commit

**Files:**
- Create: `laptop/cosmic-comp-fork.sh`
- Modify: `laptop/install.sh` (source the new script next to `cosmic-greeter-fork.sh`, line ~260)

- [ ] **Step 1: Write `laptop/cosmic-comp-fork.sh`** (mirrors `laptop/cosmic-greeter-fork.sh`)

```bash
#!/bin/bash
set -euo pipefail

# Forked cosmic-comp: adds SwitchToOutput / WorkspaceOnOutput shortcut actions for
# direct (non-directional) monitor focus on the 6-monitor layout, and makes
# ext-workspace-v1 Activate transfer keyboard focus + pointer cross-output.
#
# Forks:       https://github.com/sjawhar/cosmic-comp  (switch-to-output)
#              https://github.com/sjawhar/cosmic-settings-daemon  (output-addressed-actions)
# Upstream:    https://github.com/pop-os/cosmic-comp/issues/2120  (PR links TBD)
# Stock binary backed up at /usr/bin/cosmic-comp.stock; rollback from a TTY:
#   sudo install -m 755 /usr/bin/cosmic-comp.stock /usr/bin/cosmic-comp
#
# Once the PRs merge and ship in Pop:
#   sudo apt-mark unhold cosmic-comp && sudo apt-get upgrade cosmic-comp
#   then delete this installer and its line in laptop/install.sh.

# Package held so Pop updates don't clobber the patched binary.
if ! apt-mark showhold 2>/dev/null | grep -qx cosmic-comp; then
    echo "Holding cosmic-comp package (patched fork installed)..."
    sudo apt-mark hold cosmic-comp
fi

# dpkg -V reports a checksum mismatch on /usr/bin/cosmic-comp when the fork
# build is installed. No mismatch = stock binary = the fork needs (re)installing.
if ! dpkg -V cosmic-comp 2>/dev/null | grep -q "/usr/bin/cosmic-comp$"; then
    echo "NOTE: /usr/bin/cosmic-comp is the stock build, not the patched fork."
    echo "  git clone -b switch-to-output https://github.com/sjawhar/cosmic-comp"
    echo "  cd cosmic-comp && mise x rust@1.93.0 -- cargo build --release"
    echo "  sudo test -e /usr/bin/cosmic-comp.stock || sudo install -m 755 /usr/bin/cosmic-comp /usr/bin/cosmic-comp.stock"
    echo "  sudo install -m 755 target/release/cosmic-comp /usr/bin/cosmic-comp"
fi
```

Then `chmod +x laptop/cosmic-comp-fork.sh` and add to `laptop/install.sh` after the greeter line:

```bash
source "${LAPTOP_DIR}/cosmic-comp-fork.sh"
```

- [ ] **Step 2: Sanity-run the script**

```bash
bash laptop/cosmic-comp-fork.sh
```
Expected: no "NOTE: ... stock build" message (fork is installed and held; may print the hold message once).

- [ ] **Step 3: Commit + push dotfiles**

Everything (plan docs, shortcuts-custom, install.sh, aerospace-ws deletions, fork script) is one logical unit:

```bash
cd ~/.dotfiles
jj describe -m "laptop: cosmic-comp fork with direct monitor focus, retire aerospace-ws"
jj bookmark set main
jj git push
jj new
```
Expected: pushed to main.

---

### Task 12: Upstream PRs

- [ ] **Step 1: Push the cosmic-comp fork branch (as-tested, based on installed rev)**

```bash
cd ~/Code/cosmic-comp
jj git push --remote sjawhar --bookmark switch-to-output --allow-new
```

- [ ] **Step 2: Rebase onto upstream master for the PR**

```bash
cd ~/Code/cosmic-settings-daemon && jj git fetch && jj log -r 'trunk()' 
# settings-daemon change is tiny; rebase if trunk moved:
jj rebase -b output-addressed-actions -o 'trunk()'
mise x rust@1.93.0 -- cargo test -p cosmic-settings-config
jj git push --remote sjawhar --bookmark output-addressed-actions
```

```bash
cd ~/Code/cosmic-comp && jj git fetch
# Create a PR branch on trunk (master @ ~4d370cdf or later), keeping the deploy branch intact:
jj new 'trunk()'
# Re-apply the deploy change as a patch from its parent so upstream drift surfaces as
# conflicts/rejects instead of silently reverting trunk changes (do NOT jj restore whole files):
jj diff --git --from 'switch-to-output-' --to switch-to-output -- \
  src/input/actions.rs src/wayland/handlers/workspace.rs | patch -p1
# Inspect: the diff vs trunk must contain ONLY our intended changes:
jj diff --git --from 'trunk()' --to @
# PR branch keeps upstream's Cargo.toml (pop-os settings-daemon URL) — revert the Cargo.toml/lock repoint:
jj restore --from 'trunk()' Cargo.toml Cargo.lock
jj describe -m "shortcuts: add SwitchToOutput/WorkspaceOnOutput, focus on ext-workspace activate"
jj bookmark set switch-to-output-pr
jj git push --remote sjawhar --bookmark switch-to-output-pr --allow-new
```
Note: the PR branch will NOT build until the settings-daemon PR merges (upstream's config crate lacks the variants). That's the standard two-repo dance — mark the cosmic-comp PR draft.

- [ ] **Step 3: Open the PRs**

Style precedent: pop-os/cosmic-greeter#493 (write with the sami-voice skill). Settings-daemon PR first:

```bash
gh pr create --repo pop-os/cosmic-settings-daemon --head sjawhar:output-addressed-actions \
  --title "shortcuts: add SwitchToOutput and WorkspaceOnOutput actions" \
  --body "..." # references pop-os/cosmic-comp#2120 "Option B", explains name-or-EDID-model matching
```

Then the cosmic-comp PR (draft):

```bash
gh pr create --repo pop-os/cosmic-comp --head sjawhar:switch-to-output-pr --draft \
  --title "shortcuts: direct output focus via SwitchToOutput/WorkspaceOnOutput" \
  --body "..." # closes #2120 (Option B), notes it fixes the ext-workspace Activate focus TODO,
               # notes dependency on the settings-daemon PR + lockfile bump after it merges
```

- [ ] **Step 4: Report PR URLs to the user.** Merge/undraft follow-up happens when upstream responds — not this session's gate.

---

## Self-Review Notes

- Spec coverage: goal 1 (Tasks 2–10), goal 2's per-chord behavior via `WorkspaceOnOutput` (Tasks 2, 5 — bindings optional, user's call during testing), ext-workspace TODO (Task 6), fork playbook (Tasks 7–8, 11), keybindings + cleanup (Task 9), testing protocol (Task 10), upstream PRs (Task 12). Full "Option C" global-workspace *mode* is explicitly out of scope per spec.
- Types consistent: `switch_to_output(&mut self, &Output, Option<usize>, &Seat<State>, Serial, u32)` used identically in Tasks 4/5/6; `output_for_query(impl Iterator<Item = &Output>, &str) -> Option<Output>` defined once.
- Known risk, called out in Task 10: cosmic-settings and cosmic-settings-daemon binaries carry their *own* builds of `cosmic-settings-config` and may fail to deserialize the custom shortcuts file containing unknown variants. RON enum deserialization of an unknown variant errors; whether consumers skip the entry or drop the file is empirically tested in Task 10 and is the main go/no-go besides the focus behavior itself.
