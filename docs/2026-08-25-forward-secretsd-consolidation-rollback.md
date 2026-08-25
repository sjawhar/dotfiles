# Rolling back the forward/secretsd consolidation

`secretsd` merged into `sjawhar/forward` at v2.7.0. One tag now publishes three
artefacts: `forward` musl per target, and `secrets` native glibc x86_64.

Rollback is written down because it spans two machines and because the merged
state is *one* mise pin where the pre-merge state was two. Reverting the pin
alone leaves the other machine on the merged binary, and the two speak a
versioned wire protocol.

## What the merged state looks like

| Piece | Merged | Pre-merge |
|---|---|---|
| mise tool | `[tools.secrets]` alias -> `github:sjawhar/forward` | `"github:sjawhar/secretsd" = "latest"` |
| install dir | `~/.mise/installs/secrets/latest` | `~/.mise/installs/github-sjawhar-secretsd/latest` |
| OpenCode plugin | `secretsd@git+https://github.com/sjawhar/forward.git#v2.7.0` | `...secretsd.git#v2.6.0` |
| omp plugin | `github:sjawhar/forward#v2.7.0` | `github:sjawhar/secretsd#fb9a9954b9ac` |
| gws shim fallback | `~/.mise/installs/secrets/latest/bin/secrets` | `.../github-sjawhar-secretsd/latest/bin/secrets` |
| forward release | v2.7.0 (from the 2.6.0 line) | v0.6.1 |

The `sjawhar/secretsd` repo is archived, not deleted, and its tags v1.0.0
through v2.6.0 are also pushed to `sjawhar/forward`, so both remotes can serve
a pre-merge install.

## Rolling back

Both machines, or neither: the devbox broker and the laptop's `forward` speak a
versioned protocol, and v2.7.0's `REDEEM` reply carries a `ttl=` field that a
pre-merge `forward` does not send or expect. Mixed versions refuse grants
loudly rather than silently misbehaving, which is the intended failure, but it
does mean browser access stays down until both sides match.

1. Revert the five dotfiles edits (one commit): `mise.toml` back to the
   `"github:sjawhar/secretsd" = "latest"` entry with no `secrets` alias or
   `[tools.secrets]` block; `installers/secretsd.sh` back to the
   `github-sjawhar-secretsd` install dir and the `secretsd.git` plugin repo;
   `shims/gws` back to the old candidate path; `opencode/opencode.json` and
   `omp/plugins/package.json` back to their secretsd refs.
2. `mise install` on both machines. This restores
   `~/.mise/installs/github-sjawhar-secretsd/latest` and pins `forward` back to
   its own release line.
3. `bash install.sh` on the devbox to rewrite the systemd drop-in from the
   restored unit files, then `systemctl --user restart secretsd.socket`.
4. Laptop: `systemctl --user restart forward-daemon`.
5. Verify: `secrets grants` answers, `forward doctor` is green on both, and
   `forward browser grant --ttl 5m` blinks the key and returns a port.

Because forward's own version goes *backwards* on rollback (2.7.0 -> 0.6.1),
mise's `latest` will not pick the older release on its own. Pin explicitly:
`"github:sjawhar/forward" = "v0.6.1"` for the duration of the rollback.

## Partial rollback is not supported

Reverting only `forward` or only `secrets` leaves the pair mismatched. If the
broker is the problem, roll back both and the laptop simply loses browser
grants until the fix lands; the YubiKey PC/SC channel and the URL opener are
unaffected either way, because they do not cross the broker.
