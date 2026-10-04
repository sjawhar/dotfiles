#!/bin/bash
# Arm the 3-minute hawk-token credential refresher (scripts/hawk-token-fast,
# omp/hawk-token-refresh.{service,timer}).
#
# Standalone installer (like omp-billing-watch.sh) — not sourced by install.sh.
# Run it on a machine that runs omp sessions against the hawk/middleman gateway.
#
# Why this exists: `!hawk-token` mints live on every call and omp kills a
# `!command` after 10 s. Under this box's steady ambient load (dozens of
# concurrent agent sessions, routinely 30-45 runnable on 32 cores) an occasional
# mint takes longer than that budget — reproduced 2026-10-04 with 64 added
# stress-ng CPU hogs: a mint that normally takes ~3s took 10.1s wall clock,
# which is exactly the `No API key for provider: anthropic` / `401 invalid api
# key` failure the omp apiKey helper then reports, with no same-turn retry
# (an empty budget-exhausted mint is cached by omp as the apiKey itself).
# This timer keeps a fresh token on disk so `!hawk-token-fast` — what
# omp/models.local.yml now points at — never has to mint inside an agent turn
# in the common case; `scripts/hawk-token`'s own live-mint-with-cache-fallback
# behavior is unchanged and still runs on a cold cache or a refresher outage.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

arm_user_timer omp hawk-token-refresh
