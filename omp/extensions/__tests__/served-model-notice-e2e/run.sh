#!/usr/bin/env bash
# End-to-end check of omp/extensions/served-model-notice.ts against a real omp binary, with no
# network and no real credential: a stub Anthropic endpoint (stub.ts) and, per case, a fresh
# isolated HOME and agent dir whose config holds only the roles, the fallback chains and the
# compaction settings the case needs. Eleven cases, in a top-level session and in a task subagent
# unless marked: chain fallback, a walk below the floor (subagent only), no fallback, Anthropic's
# in-request fallback (subagent only), compaction after a fallback, refusal-cooldown.ts switching
# back, and a subagent omp starts on a replacement because its role's model has no login
# (subagent only). check.ts then prints one PASS or FAIL line per case from the transcripts and
# the request bodies.
# Usage: run.sh <directory holding the omp binary> <work dir>
# The first argument goes first on the run's PATH and omp is called by name; pass the release's own
# bin directory, not shims/, whose wrapper would add this machine's config overlay. The extensions
# tested are the installed links in ~/.omp/agent/extensions (SERVED_MODEL_NOTICE and
# REFUSAL_COOLDOWN override them). It deletes and recreates <work dir>. About three minutes.
# Rerun after an omp upgrade: the extension depends on omp emitting `retry_fallback_applied` after
# the swap, draining a steer into the retried request, and running `context` before the request.
set -uo pipefail
bindir=$1 work=$2
here=$(cd "$(dirname "$0")" && pwd)
notice=${SERVED_MODEL_NOTICE:-$HOME/.omp/agent/extensions/served-model-notice.ts}
cooldown=${REFUSAL_COOLDOWN:-$HOME/.omp/agent/extensions/refusal-cooldown.ts}
rm -rf "$work" && mkdir -p "$work"

one() { # <scenario> <case> <model> <prompt> [extra omp args...]; EXTRA_CONFIG, CASE_ENV, TASK_MODEL, OPUS5_ENTRY from the caller
	local scenario=$1 name=$2 model=$3 prompt=$4; shift 4
	local run="$work/$name" port=$((18741 + RANDOM % 2000))
	mkdir -p "$run/home" "$run/agent" "$run/work" "$run/req"
	printf 'providers:\n  anthropic:\n    baseUrl: http://127.0.0.1:%s\n    apiKey: stub-key-not-real\n' "$port" > "$run/agent/models.yml"
	cat > "$run/agent/config.yml" <<YAML
modelRoles:
  default: $model
  task: ${TASK_MODEL:-$model}
  smol: $model
retry:
  maxRetries: 1
  baseDelayMs: 10
  modelFallback: true
  fallbackChains:
    anthropic/claude-opus-5-5:
      - ${OPUS5_ENTRY:-anthropic/claude-opus-5}
      - anthropic/claude-opus-4-8
      - anthropic/claude-opus-4-7
    anthropic/claude-opus-5:
      - anthropic/claude-opus-4-8
      - anthropic/claude-opus-4-7
    anthropic/claude-fable-5-1:
      - anthropic/claude-opus-5-5
      - anthropic/claude-opus-5
      - anthropic/claude-opus-4-8
    openai-codex/gpt-5.6-terra:
      - anthropic/claude-opus-5
providers:
  anthropic:
    serverSideFallback: true
    serverSideFallbackModels:
      - claude-opus-5
      - claude-opus-4-8
${EXTRA_CONFIG:-}
YAML
	STUB_PORT=$port STUB_OUT="$run/req" STUB_SCENARIO=$scenario bun "$here/stub.ts" > "$run/stub.log" 2>&1 &
	local stub=$!
	sleep 1.5
	(cd "$run/work" && env -i PATH="$bindir:/usr/bin:/bin" HOME="$run/home" TERM=dumb NO_COLOR=1 \
		PI_CODING_AGENT_DIR="$run/agent" ${CASE_ENV:-} \
		timeout 300 omp -p "$prompt" --no-skills --no-lsp --no-title --allow-home -e "$notice" "$@" > "$run/out.txt" 2> "$run/err.txt") \
		|| echo "$name: omp exit $?"
	kill "$stub"; wait "$stub" 2>/dev/null
}

compact=$'compaction:\n  thresholdTokens: 100000\n  keepRecentTokens: 200\n  methodOrder:\n    - soft'
sub="spawn the probe" top="TOPMARKER go"
one chain chain-top anthropic/claude-opus-5-5 "$top" &
one chain chain-sub anthropic/claude-opus-5-5 "$sub" &
one deep deep-sub anthropic/claude-opus-5-5 "$sub" &
one none none-top anthropic/claude-opus-5-5 "$top" &
one none none-sub anthropic/claude-opus-5-5 "$sub" &
one inreq inreq-sub anthropic/claude-fable-5-1 "$sub" &
EXTRA_CONFIG=$compact one compact compact-top anthropic/claude-opus-5-5 "$top" &
EXTRA_CONFIG=$compact one compact compact-sub anthropic/claude-opus-5-5 "$sub" &
# The chain entry sets its own effort, so the restore must put back the session's xhigh.
OPUS5_ENTRY=anthropic/claude-opus-5:low CASE_ENV=OMP_REFUSAL_COOLDOWN_MS=1 one restore restore-top anthropic/claude-opus-5-5:xhigh "$top" -e "$cooldown" &
CASE_ENV=OMP_REFUSAL_COOLDOWN_MS=1 one restore restore-sub anthropic/claude-opus-5-5 "$sub" -e "$cooldown" &
# The task role names a Codex model this HOME has no login for, so omp starts the subagent on its chain's opus-5.
TASK_MODEL=openai-codex/gpt-5.6-terra one none startup-sub anthropic/claude-opus-5-5 "$sub" &
wait
bun "$here/check.ts" "$work"
