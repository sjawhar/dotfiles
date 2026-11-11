#!/usr/bin/env bash
# End-to-end check of omp/extensions/span-account.ts against a real omp binary, with no network and no
# real credential: a stub Anthropic endpoint and OTLP sink (stub.ts, ports 18080 and 14318), and isolated
# HOMEs holding one fake OAuth login each, seeded by seed.ts. Rerun after an omp upgrade: the extension
# depends on omp running the provider call and `after_provider_response` inside the chat span.
# Expect "with": all four attribute names, the org id, account.acct-e2e-a, and main at 1; its root uuid
# appears once more than in "without", whose trace already carries its session id. The control runs first,
# so its zero attribute-name counts prove a positive "with" count comes from this extension.
# Usage: run.sh <omp binary> <oh-my-pi checkout at the binary's commit, with node_modules> <span-account.ts> <work dir>
# It deletes and recreates <work dir>, copies the binary's native addon into the checkout's
# packages/natives/native/, and runs seed.ts from inside the checkout.
set -euo pipefail
omp=$1 checkout=$2 work=$4
ext=$(cd "$(dirname "$3")" && pwd)/$(basename "$3")
here=$(cd "$(dirname "$0")" && pwd)
rm -rf "$work" && mkdir -p "$work/with/home/.omp/agent" "$work/without/home/.omp/agent"
prepare_home() { # <run dir>
	local home=$1/home
	HOME="$home" "$omp" --smoke-test >/dev/null
	printf 'providers:\n  anthropic:\n    baseUrl: http://127.0.0.1:18080\n' > "$home/.omp/agent/models.yml"
}
seed_home() { # <run dir>
	cp "$here/seed.ts" "$checkout/spanorg-seed.ts"
	(cd "$checkout" && bun ./spanorg-seed.ts "$1/home/.omp/agent/agent.db") && rm -f "$checkout/spanorg-seed.ts"
}
prepare_home "$work/without"
cp "$work"/without/home/.omp/natives/*/pi_natives.linux-x64-modern.node "$checkout/packages/natives/native/"
seed_home "$work/without"
prepare_home "$work/with"
seed_home "$work/with"

run() { # <out dir> [extra omp args...]
	local out=$1; shift
	bun "$here/stub.ts" "$out" > "$out/stub.log" 2>&1 & local stub=$!
	sleep 2
	(cd /tmp && env -i PATH="$PATH" HOME="$out/home" TERM=dumb \
		OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:14318 OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf \
		OTEL_LOGS_EXPORTER=none OTEL_METRICS_EXPORTER=none \
		timeout 180 "$omp" -p "say ok" --model anthropic/claude-opus-5-5 "$@" > "$out/run.log" 2>&1) || echo "omp exit=$?"
	kill "$stub"; wait "$stub" 2>/dev/null || true
}
count() { cat "$1"/traces-*.bin 2>/dev/null | grep -c -a -F "$2" || true; }
root_id() { # <run dir>
	local session_file
	session_file=$(find "$1/home/.omp/agent/sessions" -type f -name '*Z_*.jsonl' -print -quit)
	if [[ $session_file =~ Z_([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}) ]]; then
		printf '%s\n' "${BASH_REMATCH[1]}"
	else
		echo "no root session id under $1/home/.omp/agent/sessions" >&2
		return 1
	fi
}

run "$work/without"
run "$work/with" -e "$ext"
for dir in without with; do
	root=$(root_id "$work/$dir")
	echo "## $dir extension (reply: $(tail -1 "$work/$dir/run.log"); root: $root)"
	for s in "chat claude-opus-5-5" omp.gen_ai.auth.organization_id 5fd73c3e-e2e0-4000-8000-00000000e2e0 \
		omp.gen_ai.auth.credential account.acct-e2e-a omp.agent.type main omp.session.root "$root" example.invalid sk-ant-oat01; do
		printf '%-40s %s\n' "$s" "$(count "$work/$dir" "$s")"
	done
done
