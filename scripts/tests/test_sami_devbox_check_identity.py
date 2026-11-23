#!/usr/bin/env python3
"""devbox/sami-devbox-check knows the plain dev box: its account lines want dev-box@theorem-agent-infra.

metadata_account_check reads the account the metadata server serves the way every client reads it
(GCE_METADATA_HOST: the host's relay, or a box's), and passes only on dev-box@; a server that still
answers dev-sjawhar@ fails it, which is the check's state before the switch. identity_limits_check
holds the four lines of what the plain dev box may not and may do: no infra-applier@ token mint (a
testIamPermissions answer with no permission, the token on curl's stdin, never its argv), no Envoy
Secret read, no exec into an Envoy pod, and `theorem-session list --all` working.

Technique, as test_sami_devbox_check_logins.py: the real script sourced without its dispatch, each
function run alone. gce_metadata_stub.py is the metadata server; `gcloud`, `curl`, `kubectl` and
`theorem-session` are stubs on PATH where a case needs them.
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import tempfile
import unittest
from collections.abc import Iterable, Mapping
from pathlib import Path

from gce_metadata_stub import MetadataStub

DOTFILES = Path(__file__).resolve().parents[2]
SCRIPT = DOTFILES / "devbox" / "sami-devbox-check"
DEV_BOX = "dev-box@theorem-agent-infra.iam.gserviceaccount.com"
OLD = "dev-sjawhar@theorem-agent-infra.iam.gserviceaccount.com"
TOKEN = "ya29.stub-box-token"

GCLOUD = f"""#!/bin/bash
[[ "$*" == "auth print-access-token" ]] && {{ echo {TOKEN}; exit 0; }}
exit 1
"""
APPLIER = "infra-applier@theorem-agent-infra.iam.gserviceaccount.com"
# The one IAM request identity_limits_check makes: a JSON POST to testIamPermissions on infra-applier@ asking for
# iam.serviceAccounts.getAccessToken, with the box's token in a config curl reads from stdin (-K -). Any other IAM
# request gets curl's HTTP-error exit, so a check that asks something else, or puts the token elsewhere, cannot pass.
# tokeninfo answers from a file named for the token; 169.254.169.254 is the VM stand-in at STUB_VM; a loopback
# server is asked for real; every other host is unreachable.
CURL = r"""#!/bin/bash
args=("$@") url="" config=no post=no json=no body="" access=""
for ((i = 0; i < ${#args[@]}; i++)); do
    case "${args[i]}" in
        -K) [[ "${args[i+1]:-}" == - ]] && config=yes ;;
        -X) [[ "${args[i+1]:-}" == POST ]] && post=yes ;;
        -H) [[ "${args[i+1]:-}" == "Content-Type: application/json" ]] && json=yes ;;
        -d) body="${args[i+1]:-}" ;;
        --data-urlencode) access="${args[i+1]#access_token=}" ;;
        http://* | https://*) url="${args[i]}" ;;
    esac
done
case "$url" in
    https://iam.googleapis.com/*)
        printf '%s\n' "$*" >>"$STUB_DIR/curl.argv"
        stdin="$(cat)"
        printf '%s\n' "$stdin" >>"$STUB_DIR/curl.stdin"
        if [[ "$url" == "https://iam.googleapis.com/v1/projects/-/serviceAccounts/@APPLIER@:testIamPermissions" \
              && "$config $post $json" == "yes yes yes" && "$body" == '{"permissions":["iam.serviceAccounts.getAccessToken"]}' \
              && "$stdin" == 'header = "Authorization: Bearer @TOKEN@"' ]]; then
            cat "$STUB_DIR/tip.json"
        else
            echo "curl: (22) The requested URL returned error: 400 (stub: not the testIamPermissions request)" >&2
            exit 22
        fi ;;
    https://oauth2.googleapis.com/tokeninfo)
        cat "$STUB_DIR/tokeninfo.$access" 2>/dev/null || { echo "curl: (22) The requested URL returned error: 400" >&2; exit 22; } ;;
    http://169.254.169.254/*)
        [[ -n "${STUB_VM:-}" ]] || { echo "curl: (7) Failed to connect: no VM stand-in" >&2; exit 7; }
        exec /usr/bin/curl "${args[@]//169.254.169.254/$STUB_VM}" ;;
    http://127.0.0.1:*) exec /usr/bin/curl "$@" ;;
    *) echo "curl: (6) Could not resolve host: $url" >&2; exit 6 ;;
esac
""".replace("@APPLIER@", APPLIER).replace("@TOKEN@", TOKEN)
KUBECTL = r"""#!/bin/bash
case "$*" in
    *"-n envoy auth can-i get secrets"*) cat "$STUB_DIR/secrets" ;;
    *"-n envoy auth can-i create pods/exec"*) cat "$STUB_DIR/exec" ;;
    *) echo "unexpected kubectl $*" >&2; exit 2 ;;
esac
"""
# Answers only `list --all`: a bare `list` shows the caller's own agents alone (theorem-labs/agent-infra#5776), so a
# check that runs it gets exit 2 and fails its line.
SESSION = r"""#!/bin/bash
[[ "$*" == "list --all" ]] && exit "$(cat "$STUB_DIR/sessions")"
exit 2
"""
# Each answer of `kubectl auth can-i` and what it makes of a line that wants a refusal: `no`, bare or with the reason GKE
# gives (as GKE answered sami-devbox on 2026-11-22 at 12:07Z), passes; a grant, nothing, an error, and a word that only
# begins with "no" fail.
CAN_I_ANSWERS = (
    ("no", "ok"),
    ('no - requires one of ["container.secrets.get"] permission(s) in Cloud IAM or a Kubernetes RBAC role with verb "get" '
     'for resource "secrets". (Error ID: CiQwMWEyMDMyNy05MmNhLTc3OGQtYTZhYS1lY2IzZWVjYzAyZDISAA==)', "ok"),
    ("yes", "FAIL"),
    ("", "FAIL"),
    ("error: You must be logged in to the server", "FAIL"),
    ("nope", "FAIL"),
)


def write_stub(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class CheckCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}", STUB_DIR=str(self.root))

    def run_function(self, call: str, **extra: str) -> list[str]:
        # From the temp directory, as test_sami_devbox_check_logins.py does: jq is a mise shim there.
        body = f'source <(sed \'/^case "${{1:-}}" in/,$d\' {SCRIPT}); {call}'
        result = subprocess.run(["bash", "-c", body], env={**self.env, **extra}, cwd=self.root, capture_output=True,
                                text=True, timeout=60, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return [line for line in result.stdout.splitlines() if line.startswith(("ok ", "FAIL ", "note "))]


class MetadataAccount(CheckCase):
    def line(self, served: str) -> str:
        stub = MetadataStub.start(served)
        self.addCleanup(stub.stop)
        [line] = self.run_function('metadata_account_check "on the host"', GCE_METADATA_HOST=stub.host)
        return line

    def test_the_relay_serving_dev_box_passes(self) -> None:
        line = self.line(DEV_BOX)
        self.assertTrue(line.startswith("ok "), line)
        self.assertIn(DEV_BOX, line)

    def test_a_server_that_still_serves_the_old_account_fails(self) -> None:
        line = self.line(OLD)
        self.assertTrue(line.startswith("FAIL "), line)
        self.assertIn(OLD, line)

    def test_no_metadata_server_fails(self) -> None:
        [line] = self.run_function('metadata_account_check "on the host"', GCE_METADATA_HOST="127.0.0.1:9")
        self.assertTrue(line.startswith("FAIL "), line)


class IdentityLimits(CheckCase):
    def setUp(self) -> None:
        super().setUp()
        for name, body in (("gcloud", GCLOUD), ("curl", CURL), ("kubectl", KUBECTL), ("theorem-session", SESSION)):
            write_stub(self.bin, name, body)
        self.answers(tip={}, secrets="no", exec_="no", sessions=0)

    def answers(self, tip: Mapping[str, object], secrets: str, exec_: str, sessions: int) -> None:
        (self.root / "tip.json").write_text(json.dumps(tip), encoding="utf-8")
        (self.root / "secrets").write_text(secrets + "\n", encoding="utf-8")
        (self.root / "exec").write_text(exec_ + "\n", encoding="utf-8")
        (self.root / "sessions").write_text(str(sessions), encoding="utf-8")

    def lines(self) -> dict[str, str]:
        out = self.run_function('identity_limits_check "on the host"')
        self.assertEqual(len(out), 4, out)
        return {line.split(" ", 1)[1].split(" on the host")[0].strip(): line.split(" ", 1)[0] for line in out}

    def test_the_plain_dev_box_passes_all_four(self) -> None:
        self.assertEqual(self.lines(), {"infra-applier closed": "ok", "no envoy secrets": "ok", "no envoy exec": "ok",
                                        "sessions": "ok"})
        # The token went to curl on stdin, in a config line, and never in its arguments.
        self.assertIn(f'header = "Authorization: Bearer {TOKEN}"', (self.root / "curl.stdin").read_text(encoding="utf-8"))
        argv = (self.root / "curl.argv").read_text(encoding="utf-8")
        self.assertNotIn(TOKEN, argv)
        self.assertIn("serviceAccounts/infra-applier@theorem-agent-infra.iam.gserviceaccount.com:testIamPermissions", argv)

    def test_the_old_account_fails_each_limit(self) -> None:
        self.answers(tip={"permissions": ["iam.serviceAccounts.getAccessToken"]}, secrets="yes", exec_="yes", sessions=1)
        self.assertEqual(set(self.lines().values()), {"FAIL"})

    def test_a_refused_or_broken_answer_is_no_pass(self) -> None:
        """An error from IAM, or from the cluster, says nothing about the grant: each is a FAIL."""
        error = {"error": {"code": 401, "message": "Request had invalid authentication credentials.", "status": "UNAUTHENTICATED"}}
        self.answers(tip=error, secrets="error: You must be logged in to the server", exec_="", sessions=0)
        got = self.lines()
        self.assertEqual((got["infra-applier closed"], got["no envoy secrets"], got["no envoy exec"]), ("FAIL", "FAIL", "FAIL"))

    def test_a_refusal_with_or_without_its_reason_passes_and_every_other_answer_fails(self) -> None:
        for answer, want in CAN_I_ANSWERS:
            with self.subTest(answer=answer):
                self.answers(tip={}, secrets=answer, exec_=answer, sessions=0)
                got = self.lines()
                self.assertEqual((got["no envoy secrets"], got["no envoy exec"]), (want, want))

    def test_no_theorem_session_on_path_fails_the_sessions_line_naming_its_installer(self) -> None:
        (self.bin / "theorem-session").unlink()
        self.env["PATH"] = f"{self.bin}:/usr/bin:/bin"
        [line] = [line for line in self.run_function('identity_limits_check "on the host"') if " sessions " in line]
        self.assertTrue(line.startswith("FAIL "), line)
        self.assertIn("installers/theorem-session.sh installs it", line)

    def test_a_cut_short_iam_answer_is_no_pass_even_if_the_json_is_complete(self) -> None:
        write_stub(self.bin, "curl", '#!/bin/bash\ncat >/dev/null\nprintf "{}"\nexit 18\n')
        self.assertEqual(self.lines()["infra-applier closed"], "FAIL")



# Each stub answers only the exact target it is asked about, from a file named for that target, so a
# check that asks about the wrong cluster, namespace, account or bucket gets "unexpected" and fails.
ROUTED_KUBECTL = r"""#!/bin/bash
ctx="" ns="" args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
    case "${args[i]}" in --context) ctx="${args[i+1]}" ;; -n) ns="${args[i+1]}" ;; esac
done
[[ "$*" == *"auth can-i create jobs"* && -f "$STUB_DIR/kubectl.$ctx.$ns" ]] || { echo "unexpected kubectl $*" >&2; exit 2; }
answer="$(cat "$STUB_DIR/kubectl.$ctx.$ns")"
echo "$answer"
[[ "$answer" == yes ]]
"""
ROUTED_GCLOUD = r"""#!/bin/bash
printf '%s\n' "$*" >>"$STUB_DIR/gcloud.argv"
answer() {
    case "$(cat "$STUB_DIR/$1" 2>/dev/null || echo unexpected)" in
        ok) return 0 ;;
        refused) echo "ERROR: (gcloud) PERMISSION_DENIED: Permission 'iam.serviceAccounts.x' denied on resource" >&2; exit 1 ;;
        transport) echo "ERROR: (gcloud) There was a problem refreshing your current auth tokens: Connection reset by peer" >&2; exit 1 ;;
        *) echo "unexpected gcloud $*" >&2; exit 2 ;;
    esac
}
case "$*" in
    "auth print-access-token --impersonate-service-account="*)
        answer "mint.${3#*=}"; printf 'ya29.%0200d\n' 0 ;;
    "iam service-accounts sign-blob --iam-account="*)
        answer "sign.${4#*=}"; printf 'signature' >"${@: -1}" ;;
    "storage ls gs://theorem-ossv-runs/out/ossv/") answer ls.ossv-runs; echo gs://theorem-ossv-runs/out/ossv/x/ ;;
    *) echo "unexpected gcloud $*" >&2; exit 2 ;;
esac
"""
AZ_READER = r"""#!/bin/bash
printf '%s|%s\n' "${AZ_READER_REASON:-}" "$*" >>"$STUB_DIR/az-reader.argv"
[[ "$*" == "account show --query "*" -o json" ]] || { echo "unexpected az-reader $*" >&2; exit 2; }
# The real reader prints the broker's notice on stderr before az's answer on stdout (seen on sami-devbox).
echo "agent-secrets: METADATA_READ from the broker AGENT_SECRETS_URL names (GCP Secret Manager)" >&2
cat "$STUB_DIR/az-reader"
"""
DISPATCH = "gke_theorem-agent-infra_us-west1_dispatch"
GRADE = "gke_theorem-agent-infra_us-central1_theorem-us-central1"
FARM_SIGNER = "ossv-runs-signer@theorem-agent-infra.iam.gserviceaccount.com"
BOX_SIGNER = "build-io-signer@theorem-agent-infra.iam.gserviceaccount.com"

# Each answer of `kubectl -n grade auth can-i create jobs` and what the grade pool line makes of it: a grant passes; a
# refusal, bare or with GKE's reason, nothing, an error, and a word that is no grant fail.
GRADE_POOL_ANSWERS = (
    ("yes", "ok"),
    ("no", "FAIL"),
    (next(answer for answer, _ in CAN_I_ANSWERS if answer.startswith("no - ")), "FAIL"),
    ("", "FAIL"),
    ("error: You must be logged in to the server", "FAIL"),
    ("nope", "FAIL"),
)


class GradePool(CheckCase):
    """Each team account may submit a grade in namespace grade (R2 of dispatch://INFRA-4710), so only yes passes."""

    def test_the_grade_pool_line_passes_only_on_yes(self) -> None:
        write_stub(self.bin, "kubectl", ROUTED_KUBECTL)
        for answer, want in GRADE_POOL_ANSWERS:
            with self.subTest(answer=answer):
                _ = (self.root / f"kubectl.{GRADE}.grade").write_text(answer + "\n", encoding="utf-8")
                out = self.run_function('grade_pool_check "in the box"')
                self.assertEqual(len(out), 1, out)
                self.assertEqual(out[0].split(" ", 1)[0], want, out[0])


class RoutedOffTheBox(CheckCase):
    """Uses the move census routes off the box: the box's own answer is a refusal."""

    LABELS = ("N6 signer", "N6 bucket", "N6 farm")

    def setUp(self) -> None:
        super().setUp()
        for name, body in (("kubectl", ROUTED_KUBECTL), ("gcloud", ROUTED_GCLOUD), ("az-reader", AZ_READER)):
            write_stub(self.bin, name, body)
        self.refused_everywhere()

    def set(self, name: str, value: str) -> None:
        (self.root / name).write_text(value + "\n", encoding="utf-8")

    def refused_everywhere(self) -> None:
        self.set(f"kubectl.{DISPATCH}.builds-ossv", "no")
        self.set(f"mint.{FARM_SIGNER}", "refused")
        self.set(f"sign.{FARM_SIGNER}", "refused")
        self.set("ls.ossv-runs", "refused")

    def lines(self) -> dict[str, str]:
        out = self.run_function('n6_routes_check "in the box"')
        self.assertEqual(len(out), 3, out)
        return {label: line.split(" ", 1)[0] for label in self.LABELS for line in out if f" {label} " in f" {line} "}

    def test_the_plain_dev_box_is_refused_the_farms_signer_bucket_and_namespace(self) -> None:
        self.assertEqual(self.lines(), dict.fromkeys(self.LABELS, "ok"))

    def test_each_reach_fails_only_its_own_line(self) -> None:
        for label, name, value in (("N6 farm", f"kubectl.{DISPATCH}.builds-ossv", "yes"),
                                   ("N6 bucket", "ls.ossv-runs", "ok"),
                                   ("N6 signer", f"mint.{FARM_SIGNER}", "ok"),
                                   ("N6 signer", f"sign.{FARM_SIGNER}", "ok")):
            with self.subTest(name=name):
                self.refused_everywhere()
                self.set(name, value)
                want = {other: ("FAIL" if other == label else "ok") for other in self.LABELS}
                self.assertEqual(self.lines(), want)

    def test_a_failure_that_is_no_refusal_is_no_pass(self) -> None:
        """A transport error of the mint, beside a real refusal of sign-blob, says nothing about the mint's grant.
        So does a bucket error that is not a 403."""
        for label, name, value in (("N6 signer", f"mint.{FARM_SIGNER}", "transport"),
                                   ("N6 signer", f"sign.{FARM_SIGNER}", "transport"),
                                   ("N6 bucket", "ls.ossv-runs", "transport")):
            with self.subTest(name=name, value=value):
                self.refused_everywhere()
                self.set(name, value)
                self.assertEqual(self.lines()[label], "FAIL")

    def test_a_refusal_with_or_without_its_reason_passes_and_every_other_answer_fails(self) -> None:
        for answer, want in CAN_I_ANSWERS:
            with self.subTest(answer=answer):
                self.refused_everywhere()
                self.set(f"kubectl.{DISPATCH}.builds-ossv", answer)
                self.assertEqual(self.lines()["N6 farm"], want)

    def test_the_box_signs_as_build_io_signer(self) -> None:
        """Census row U23's exception: dev-box@ holds Token Creator on build-io-signer@ alone, so the box mints its token
        and signs as it. A refusal of either fails the line."""
        for mint, sign, want in (("ok", "ok", "ok"), ("refused", "ok", "FAIL"), ("ok", "refused", "FAIL"),
                                 ("transport", "ok", "FAIL")):
            with self.subTest(mint=mint, sign=sign):
                self.set(f"mint.{BOX_SIGNER}", mint)
                self.set(f"sign.{BOX_SIGNER}", sign)
                [line] = self.run_function('n6_box_signer_check "in the box"')
                self.assertEqual(line.split(" ", 1)[0], want, line)

    def test_a_signer_line_never_prints_a_minted_token(self) -> None:
        """The check's log is a file: a line that fails after a mint succeeded names the token's length, never the
        token."""
        token = "ya29." + "0" * 200
        self.set(f"mint.{BOX_SIGNER}", "ok")
        self.set(f"sign.{BOX_SIGNER}", "refused")
        self.set(f"mint.{FARM_SIGNER}", "ok")
        out = self.run_function('n6_box_signer_check "in the box"; n6_routes_check "in the box"')
        signer_lines = [line for line in out if " N6 signer " in f" {line} "]
        self.assertEqual(len(signer_lines), 2, out)
        for line in signer_lines:
            self.assertTrue(line.startswith("FAIL "), line)
            self.assertNotIn("ya29.", line)

    def test_the_ssh_path_kubectl_line_carries_the_relay_and_reads_a_namespace_the_team_set_reads(self) -> None:
        """A command sshd runs gets the three GCE_METADATA_* values from .bashrc, so the line that stands in for one
        passes them on. It lists Jobs in builds, which the team set reads (census row U07), not in builds-ossv: one
        request for one Job, whose answer counts the rest (`--chunk-size 1` paged 1,910 Jobs on 2026-11-22 until its
        continue token expired)."""
        # env -i drops STUB_DIR, so the stub writes to a path baked into it.
        write_stub(self.bin, "kubectl", '#!/bin/bash\nprintf "%s\\n" "$*" "HOST=${GCE_METADATA_HOST:-}" '
                   f'"ROOT=${{GCE_METADATA_ROOT:-}}" "IP=${{GCE_METADATA_IP:-}}" >"{self.root}/kubectl.seen"\n'
                   """echo '{"kind":"JobList","items":[{}],"metadata":{"remainingItemCount":1909}}'\n""")
        relay = {name: "127.0.0.1:8170" for name in ("GCE_METADATA_HOST", "GCE_METADATA_ROOT", "GCE_METADATA_IP")}
        [line] = self.run_function(f'SSH_PATH="{self.bin}:$SSH_PATH"; host_plain_path_kubectl', **relay)
        self.assertTrue(line.startswith("ok "), line)
        self.assertIn("(1910 jobs)", line)
        seen = (self.root / "kubectl.seen").read_text(encoding="utf-8").splitlines()
        self.assertIn("get --raw /apis/batch/v1/namespaces/builds/jobs?limit=1", seen[0])
        self.assertEqual(seen[1:], ["HOST=127.0.0.1:8170", "ROOT=127.0.0.1:8170", "IP=127.0.0.1:8170"])

    def test_an_ssh_path_kubectl_answer_that_is_no_job_list_fails(self) -> None:
        """An error, and an exit 0 whose answer is no JobList (a Status, nothing), say nothing about the list."""
        for body in ('echo "Error from server (Expired): continue token expired" >&2; exit 1',
                     """echo '{"kind":"Status","status":"Failure"}'""", "exit 0"):
            with self.subTest(body=body):
                write_stub(self.bin, "kubectl", f"#!/bin/bash\n{body}\n")
                [line] = self.run_function(f'SSH_PATH="{self.bin}:$SSH_PATH"; host_plain_path_kubectl')
                self.assertTrue(line.startswith("FAIL "), line)

    def test_azure_reads_go_through_az_reader(self) -> None:
        (self.root / "az-reader").write_text(json.dumps({"user": "legion-reader", "type": "servicePrincipal",
                                                         "subscription": "s"}), encoding="utf-8")
        [line] = self.run_function("azure_check")
        self.assertTrue(line.startswith("ok "), line)
        reason, args = (self.root / "az-reader.argv").read_text(encoding="utf-8").strip().split("|", 1)
        self.assertTrue(reason, "az-reader needs AZ_READER_REASON")
        self.assertTrue(args.startswith("account show --query"), args)

    def test_a_refused_az_reader_fails_the_azure_line(self) -> None:
        (self.root / "az-reader").write_text("AADSTS700213: No matching federated identity record found\n", encoding="utf-8")
        [line] = self.run_function("azure_check")
        self.assertTrue(line.startswith("FAIL "), line)


# Stubs for whole runs of host() and in_box(), and for boxid_check and host_identity alone. Each answers only the exact
# target it is asked about, from a file named for it; anything else is "unexpected" and fails its line.
BOXID_TOKEN = "ya29.stub-boxid-token"
BOX_GCLOUD = r"""#!/bin/bash
case "$*|${CLOUDSDK_CONFIG:-}" in
    "auth print-access-token|$STUB_BOXID/gcloud-box")
        [[ "$(cat "$STUB_DIR/gcloud.boxid")" == ok ]] && { echo @BOXID_TOKEN@; exit 0; }
        echo "ERROR: (gcloud.auth.print-access-token) There was a problem refreshing your current auth tokens" >&2; exit 1 ;;
    "auth print-access-token|") echo @TOKEN@ ;;
    "auth list --filter=status:ACTIVE --format=value(account)|") echo @DEV_BOX@ ;;
    *) echo "unexpected gcloud $* (CLOUDSDK_CONFIG=${CLOUDSDK_CONFIG:-})" >&2; exit 2 ;;
esac
""".replace("@BOXID_TOKEN@", BOXID_TOKEN).replace("@TOKEN@", TOKEN).replace("@DEV_BOX@", DEV_BOX)
BOX_KUBECTL = r"""#!/bin/bash
all="$*" ctx="" ns="" kc="" rest=()
while (( $# )); do
    case "$1" in
        --context) ctx="$2"; shift 2 ;;
        --kubeconfig) kc="$2"; shift 2 ;;
        -n) ns="$2"; shift 2 ;;
        *) rest+=("$1"); shift ;;
    esac
done
case "${rest[*]}|$ns|$ctx|$kc|${CLOUDSDK_CONFIG:-}" in
    "auth can-i get secrets|envoy|@DISPATCH@||"*) answer=envoy-secrets ;;
    "auth can-i create pods/exec|envoy|@DISPATCH@||"*) answer=envoy-exec ;;
    "auth can-i create jobs|grade|@GRADE@||"*) answer=grade ;;
    "auth can-i create jobs|builds||$STUB_BOXID/infra-kubeconfig|$STUB_BOXID/gcloud-box") answer=boxid ;;
    *) echo "unexpected kubectl $all (CLOUDSDK_CONFIG=${CLOUDSDK_CONFIG:-})" >&2; exit 2 ;;
esac
got="$(cat "$STUB_DIR/kubectl.$answer")"
echo "$got"
[[ "$got" == yes ]]
""".replace("@DISPATCH@", DISPATCH).replace("@GRADE@", GRADE)
# timeout [-k N] DURATION CMD...: the command alone; a raw TCP probe of a tailnet name fails, as with no network.
TIMEOUT = r"""#!/bin/bash
[[ "$1" == -k ]] && shift 2
shift
[[ "$*" == *"/dev/tcp/"* ]] && exit 1
exec "$@"
"""
TAILSCALE = """#!/bin/bash
echo '{"Self": {"HostName": "sami-devbox", "Tags": [], "UserID": 7}, "User": {"7": {"LoginName": "sami@theorem.dev"}}}'
"""
ID = """#!/bin/bash
case "$*" in -u) echo 1000 ;; -un) echo sami ;; *) exec /usr/bin/id "$@" ;; esac
"""
SUMMARY = re.compile(r"^== (?:box done, )?(\d+) failed")


class WholeRunCase(CheckCase):
    """The real script with PATH holding only stubs, /usr/bin and /bin, a throwaway HOME and DOTFILES_DIR, the relay
    (GCE_METADATA_HOST) serving dev-box@ and the VM's own server (STUB_VM) still dev-sjawhar@."""

    BOX_TOKEN_EMAIL: str | None = DEV_BOX

    def setUp(self) -> None:
        super().setUp()
        home = self.root / "home"
        home.mkdir()
        dotfiles = self.root / "dotfiles"
        (dotfiles / "scripts").mkdir(parents=True)
        write_stub(dotfiles / "scripts", "agentbox", "#!/bin/bash\nexit 0\n")
        ssh_bin = self.root / "ssh-bin"
        ssh_bin.mkdir()
        write_stub(ssh_bin, "kubectl", """#!/bin/bash\necho '{"kind":"JobList","items":[{}],"metadata":{}}'\n""")
        for name, body in (("gcloud", BOX_GCLOUD), ("curl", CURL), ("kubectl", BOX_KUBECTL), ("theorem-session", SESSION),
                           ("az-reader", AZ_READER), ("timeout", TIMEOUT), ("tailscale", TAILSCALE), ("id", ID),
                           ("hostname", "#!/bin/bash\necho sami-devbox\n"), ("secrets", "#!/bin/bash\nexit 1\n")):
            write_stub(self.bin, name, body)
        self.env = {"HOME": str(home), "PATH": f"{self.bin}:/usr/bin:/bin", "STUB_DIR": str(self.root),
                    "STUB_BOXID": str(self.root / "boxid"), "STUB_SSH_BIN": str(ssh_bin), "DOTFILES_DIR": str(dotfiles),
                    "GCE_METADATA_HOST": self.metadata(DEV_BOX), "STUB_VM": self.metadata(OLD), "AGENTBOX_BOX": "stub-box"}
        self.defaults()

    def metadata(self, account: str) -> str:
        stub = MetadataStub.start(account)
        self.addCleanup(stub.stop)
        return stub.host

    def set(self, name: str, value: str) -> None:
        (self.root / name).write_text(value + "\n", encoding="utf-8")

    def defaults(self) -> None:
        """Every identity answer of the plain dev box."""
        for name, value in (("tip.json", "{}"), ("kubectl.envoy-secrets", "no"), ("kubectl.envoy-exec", "no"),
                            ("kubectl.grade", "yes"), ("kubectl.boxid", "yes"), ("sessions", "0"), ("gcloud.boxid", "ok"),
                            (f"tokeninfo.{BOXID_TOKEN}", json.dumps({"email": DEV_BOX})),
                            ("az-reader", json.dumps({"user": "legion-reader", "type": "servicePrincipal", "subscription": "s"}))):
            self.set(name, value)
        (self.root / f"tokeninfo.{TOKEN}").unlink(missing_ok=True)
        if self.BOX_TOKEN_EMAIL is not None:
            self.set(f"tokeninfo.{TOKEN}", json.dumps({"email": self.BOX_TOKEN_EMAIL}))

    def run_script(self, call: str, unrelated: str = "", **extra: str) -> tuple[list[str], int]:
        """`call` after sourcing the script, with each function named in `unrelated` made a no-op."""
        body = (f'source <(sed \'/^case "${{1:-}}" in/,$d\' {SCRIPT}); BOXID_DIR="$STUB_BOXID"; SSH_PATH="$STUB_SSH_BIN"; '
                f'for f in {unrelated}; do eval "$f() {{ :; }}"; done; {call}')
        result = subprocess.run(["bash", "-c", body], env={**self.env, **extra}, cwd=self.root, capture_output=True,
                                text=True, timeout=120, check=False)
        return result.stdout.splitlines(), result.returncode

    def statuses(self, out: list[str], labels: Iterable[str]) -> dict[str, str]:
        """The status (ok, FAIL, note) of the one line whose text starts with each label."""
        got: dict[str, str] = {}
        for label in labels:
            found = [line.split(None, 1)[0] for line in out
                     if line.startswith(("ok ", "FAIL ", "note ")) and line.split(None, 1)[1].startswith(label)]
            self.assertEqual(len(found), 1, (label, out))
            got[label] = found[0]
        return got


class TheRunsCallTheIdentityChecks(WholeRunCase):
    """host() and in_box() run the identity checks, and each failed check is one more FAIL in the run's own count.
    Every function that is no identity check is a no-op; the run's other inline lines fail the same way each time,
    so a fault changes exactly its own line and adds exactly one to the count."""

    HOST_UNRELATED = ("host_disks host_rootfs host_units host_packages host_bwrap host_nofile dispatch_cli_check "
                      "mise_check host_logins host_omp_logins host_owners host_guard github_check tq_check "
                      "leak_check_host_check prepush_hook_host_check global_hooks_host_check ossv_tree_host_check n6_check "
                      "host_kernel")
    BOX_UNRELATED = ("moved_gcloud_config_check gateway_check tools_check github_check tq_check n6_check host_sshd_check "
                     "dispatch_cli_check")
    HOST = {"host: hostname": "ok", "host: user": "ok", "tailnet:": "ok", "metadata account on the host": "ok",
            "host: the VM's attached account": "note", "host: no box_credential_config.json": "ok",
            "infra-applier closed on the host": "ok", "no envoy secrets on the host": "ok",
            "no envoy exec on the host": "ok", "sessions on the host": "ok", "ssh-PATH kubectl": "ok"}
    BOX = {"metadata account in the box": "ok", "infra-applier closed in the box": "ok", "no envoy secrets in the box": "ok",
           "no envoy exec in the box": "ok", "sessions in the box": "ok", "grade pool in the box": "ok",
           "boxid: CLOUDSDK_CONFIG=": "ok", "boxid: kubectl --kubeconfig": "ok", "Azure:": "ok"}

    def count(self, out: list[str]) -> int:
        """The run's own failure count, which must be the number of FAIL lines it printed."""
        counts = [int(match[1]) for line in out if (match := SUMMARY.match(line))]
        self.assertEqual(len(counts), 1, out)
        self.assertEqual(counts[0], sum(line.startswith("FAIL ") for line in out), out)
        return counts[0]

    def faults(self, where: str) -> list[tuple[str, str, str]]:
        """(the line that must fail, the answer file or variable, its value): one identity fault each."""
        return [(f"metadata account {where}", "GCE_METADATA_HOST", self.metadata(OLD)),
                (f"infra-applier closed {where}", "tip.json", json.dumps({"permissions": ["iam.serviceAccounts.getAccessToken"]})),
                (f"no envoy secrets {where}", "kubectl.envoy-secrets", "yes"),
                (f"no envoy exec {where}", "kubectl.envoy-exec", "yes"),
                (f"sessions {where}", "sessions", "1")]

    def assert_each_fault_fails_its_line(self, call: str, unrelated: str, want: dict[str, str],
                                         faults: list[tuple[str, str, str]]) -> None:
        out, _ = self.run_script(call, unrelated)
        self.assertEqual(self.statuses(out, want), want, out)
        clean = self.count(out)
        for label, name, value in faults:
            with self.subTest(fault=label):
                self.defaults()
                extra = {name: value} if name.isupper() else {}
                if not extra:
                    self.set(name, value)
                out, rc = self.run_script(call, unrelated, **extra)
                self.assertEqual(self.statuses(out, want), {**want, label: "FAIL"}, out)
                self.assertEqual(self.count(out), clean + 1)
                self.assertNotEqual(rc, 0)

    def test_host_runs_the_identity_checks(self) -> None:
        self.assert_each_fault_fails_its_line("host", self.HOST_UNRELATED, self.HOST, self.faults("on the host"))

    def test_in_box_runs_the_identity_checks(self) -> None:
        faults = self.faults("in the box") + [("grade pool in the box", "kubectl.grade", "no"),
                                              ("boxid: kubectl --kubeconfig", "kubectl.boxid", "no"),
                                              ("boxid: CLOUDSDK_CONFIG=", "gcloud.boxid", "error"),
                                              ("Azure:", "az-reader", "AADSTS700213: No matching federated identity record found")]
        self.assert_each_fault_fails_its_line("in_box", self.BOX_UNRELATED, self.BOX, faults)


class BoxidCheck(WholeRunCase):
    """boxid_check asks through the boxid config dir and kubeconfig: a token Google names as dev-box@'s, and the
    build pool's submit in builds. The box's own token names no one here, so a call that drops CLOUDSDK_CONFIG fails."""

    BOX_TOKEN_EMAIL = None
    LABELS = ("boxid: CLOUDSDK_CONFIG=", "boxid: kubectl --kubeconfig")

    def lines(self) -> list[str]:
        out, rc = self.run_script("boxid_check")
        self.assertEqual(rc, 0, out)
        return list(self.statuses(out, self.LABELS).values())

    def test_the_boxid_dir_gives_a_dev_box_token_and_a_build_submit(self) -> None:
        self.assertEqual(self.lines(), ["ok", "ok"])

    def test_each_answer_fails_only_its_own_line(self) -> None:
        for name, value, want in (("kubectl.boxid", "no", ["ok", "FAIL"]),
                                  ("kubectl.boxid", "error: You must be logged in to the server", ["ok", "FAIL"]),
                                  ("gcloud.boxid", "error", ["FAIL", "ok"]),
                                  (f"tokeninfo.{BOXID_TOKEN}", json.dumps({"email": OLD}), ["FAIL", "ok"])):
            with self.subTest(name=name, value=value):
                self.defaults()
                self.set(name, value)
                self.assertEqual(self.lines(), want)


class HostIdentity(WholeRunCase):
    """host_identity passes on the account the relay serves, and names the VM's own attached account, read from the
    real server's address, in a note until the VM's account changes."""

    LABELS = ("host: hostname", "host: user", "tailnet:", "metadata account on the host", "host: the VM's attached account",
              "host: no box_credential_config.json")

    def run_identity(self, **extra: str) -> tuple[dict[str, str], list[str], int]:
        out, rc = self.run_script('host_identity; echo "fails=$fails"', **extra)
        self.assertEqual(rc, 0, out)
        [fails] = [int(line.split("=", 1)[1]) for line in out if line.startswith("fails=")]
        return self.statuses(out, self.LABELS), out, fails

    def test_the_relay_serving_dev_box_passes_and_the_vms_own_account_is_a_note(self) -> None:
        got, out, fails = self.run_identity()
        self.assertEqual(got, {**dict.fromkeys(self.LABELS, "ok"), "host: the VM's attached account": "note"})
        self.assertEqual(fails, 0)
        [note] = [line for line in out if line.startswith("note ")]
        self.assertIn(OLD, note)

    def test_a_vm_that_already_runs_as_dev_box_passes_both(self) -> None:
        got, _, fails = self.run_identity(STUB_VM=self.metadata(DEV_BOX))
        self.assertEqual(got, dict.fromkeys(self.LABELS, "ok"))
        self.assertEqual(fails, 0)

    def test_a_relay_still_serving_the_old_account_fails(self) -> None:
        got, _, fails = self.run_identity(GCE_METADATA_HOST=self.metadata(OLD))
        self.assertEqual(got["metadata account on the host"], "FAIL")
        self.assertEqual(fails, 1)


if __name__ == "__main__":
    unittest.main()
