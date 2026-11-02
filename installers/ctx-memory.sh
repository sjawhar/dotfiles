#!/bin/bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# ctx-memory - the team memory graph. The client is a mise pin
# ("github:sjawhar/ctx-memory"), the reading skill an omp plugin
# (omp/plugins/package.json), and shims/ctx names the server. This installer
# adds the standing rule that tells a session the graph exists.
#
# Why a rule and not only the skill: omp shows the model a 12-word hint of each
# skill's description and nothing else until the skill is read, so a session
# that does not already know the graph exists has no reason to read it. Sami
# asked for the rule beside a better skill description
# (dispatch://INFRA-574/ask/c4e1d3d3-4de3-45ae-a8a0-da1c091ccecb,
# 2026-11-02): "we can also reinforce it with a standing instruction".
#
# Only on a machine that reaches the server: the rule's body is carried on
# every request of every session, and on a machine without the graph it would
# tell each session to run a command that cannot work. A server that does not
# answer right now leaves an installed rule in place rather than removing it,
# so an outage does not change what sessions are told.
OMP_RULES_DIR="${HOME}/.omp/agent/rules"
CTX_RULE="${OMP_RULES_DIR}/memory-graph.md"
CTX_SERVER_URL="${CTX_SERVER:-http://ctx-memory.tail12ec1c.ts.net}"

if [ -n "$CTX_SERVER_URL" ] && curl -fsS --max-time 5 -o /dev/null "${CTX_SERVER_URL}/healthz" 2> /dev/null; then
    mkdir -p "$OMP_RULES_DIR"
    ensure_link "${DOTFILES_DIR}/omp/rules/memory-graph.md" "$CTX_RULE"
elif [ -e "$CTX_RULE" ]; then
    echo "ctx-memory: ${CTX_SERVER_URL} did not answer; leaving the installed rule in place" >&2
fi
