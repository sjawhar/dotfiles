#!/usr/bin/env bash
# End-to-end check of omp/extensions/span-account.ts against a real omp binary, with no network and no
# real credential: a stub Anthropic endpoint and OTLP sink (stub.ts, ports 18080 and 14318), and an
# isolated HOME holding one fake OAuth login seeded by seed.ts. Rerun after an omp upgrade: the
# extension depends on omp running the provider call and `assistant_message` inside the chat span.
# Expect "with": both attribute names, the org id and account.acct-e2e-a at 1, the token and email at 0;
# "without": 0 for all but the chat span.
# Usage: run.sh <omp binary> <oh-my-pi checkout at the binary's commit, with node_modules> <span-account.ts> <work dir>
# It deletes and recreates <work dir>, copies the binary's native addon into the checkout's
# packages/natives/native/, and runs seed.ts from inside the checkout.
set -euo pipefail
omp=$1 checkout=$2 ext=$3 work=$4
here=$(cd "$(dirname "$0")" && pwd)
rm -rf "$work" && mkdir -p "$work/home/.omp/agent" "$work/with" "$work/without"
HOME="$work/home" "$omp" --smoke-test >/dev/null
cp "$work"/home/.omp/natives/*/pi_natives.linux-x64-modern.node "$checkout/packages/natives/native/"
cp "$here/seed.ts" "$checkout/spanorg-seed.ts"
(cd "$checkout" && bun ./spanorg-seed.ts "$work/home/.omp/agent/agent.db") && rm -f "$checkout/spanorg-seed.ts"
printf 'providers:\n  anthropic:\n    baseUrl: http://127.0.0.1:18080\n' > "$work/home/.omp/agent/models.yml"

run() { # <out dir> [extra omp args...]
	local out=$1; shift
	bun "$here/stub.ts" "$out" > "$out/stub.log" 2>&1 & local stub=$!
	sleep 2
	(cd /tmp && env -i PATH="$PATH" HOME="$work/home" TERM=dumb \
		OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:14318 OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf \
		OTEL_LOGS_EXPORTER=none OTEL_METRICS_EXPORTER=none \
		timeout 180 "$omp" -p "say ok" --model anthropic/claude-opus-5-5 --no-session "$@" > "$out/run.log" 2>&1) || echo "omp exit=$?"
	kill "$stub"; wait "$stub" 2>/dev/null || true
}
count() { cat "$1"/traces-*.bin 2>/dev/null | grep -c -a -F "$2" || true; }

run "$work/with" -e "$ext"
run "$work/without"
for dir in with without; do
	echo "## $dir the extension (reply: $(tail -1 "$work/$dir/run.log"))"
	for s in "chat claude-opus-5-5" omp.gen_ai.auth.organization_id 5fd73c3e-e2e0-4000-8000-00000000e2e0 \
		omp.gen_ai.auth.credential account.acct-e2e-a example.invalid sk-ant-oat01; do
		printf '%-40s %s\n' "$s" "$(count "$work/$dir" "$s")"
	done
done
