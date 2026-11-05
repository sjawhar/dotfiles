#!/usr/bin/env python3
"""shims/git: a `git push` that jj runs gets the shared public leak check wherever git's pre-push hook would give it.

jj runs git with --no-verify, so no hook runs for a jj push. In a guarded checkout (one with a remote whose GitHub
repository is public or of unreadable visibility, dispatch://BET-706) shims/git drops that flag and points
core.hooksPath at shims/jj-push-hooks/, whose pre-push makes the installed hook's call to the check, with git's own URL
and update lines. The real jj and the real git do every step here, with this shims dir ahead of every other git and jj
on PATH. Most remotes are reached through a remote helper (`remote.<name>.vcs=sink`, `git-remote-sink` below): git
keeps the URL as written, hands it to the hook, and the helper connects git to a local bare repository named after that
URL. The cases that need git's own transports use a real local path, or ssh through a local-only stand-in
(`SSH_ADAPTER`). GIT_ALLOW_PROTOCOL allows only those, and a fake gh, first on PATH (ahead of shims/gh too, which
scripts/github-repo-visibility runs by path and which hands the call to the first gh on PATH), answers nothing. So no
test can reach GitHub, even when the gate wrongly lets a push through, and each sink shows exactly which commit reached
which URL. A fake ssh, also first on PATH, runs the real one with the test's own config (SSH_CONFIG), so `ssh -G`
resolves the same aliases on every machine. The check is the installed one, found the way the shim and the pre-push
hook find it; HOME is the test's own, so the visibility cache is the test's.
"""
import hashlib
import os
import re
import resource
import shutil
import statistics
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_jj_lock_shim import real_jj, run_bounded

SHIMS = Path(__file__).parents[1]
ROCQ = "https://github.com/theorem-labs/rocq.git"
# A private repository of ours: the check passes any push to it.
PRIVATE = "https://github.com/theorem-labs/private-fixture.git"


def leak_check() -> Path:
    """The shared check the way the shim finds it: PUBLIC_LEAK_CHECK, PATH, then the machine pointer."""
    for c in (os.environ.get("PUBLIC_LEAK_CHECK"), shutil.which("public-leak-check.sh")):
        if c and os.access(c, os.X_OK):
            return Path(c)
    pointer = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "public-leak-check" / "path"
    if pointer.is_file():
        c = pointer.read_text(encoding="utf-8").strip()
        if c and os.access(c, os.X_OK):
            return Path(c)
    raise unittest.SkipTest("no public-leak-check.sh on this machine")


# The customer canary as each kind of check holds it: the shell check (leak-check-v2..v4) as a self-test line
# `customer|graded on <name>, ...`; the Python check (public_leak_check.py, beside the public-leak-check.sh launcher
# since the rewrite) as the text of its `customer-platform` canary generator, `b"graded on <name>, ...`. Neither has
# a mode that prints its canaries (v8's main() knows check-public-text, --commits, --tree, gh-guard and REMOTE URL).
CANARY_FORMS = (re.compile(r"^customer\|graded on ([A-Za-z]+),", re.M),
                re.compile(r"""["']customer-platform["'],\s*b["']graded on ([A-Za-z]+), """))


def customer_name(check: Path) -> str:
    """The customer's name, read from the installed check's own self-test canary: in public_leak_check.py beside the
    launcher (resolved through links, as the launcher finds it) when that file is there, else in the check itself.
    This file lands in a public repository, so it never names the customer; the leaking text is built from this at
    run time."""
    py = check.resolve().parent / "public_leak_check.py"
    source = py if py.is_file() else check
    text = source.read_text(encoding="utf-8")
    for form in CANARY_FORMS:
        m = form.search(text)
        if m:
            return m.group(1)
    raise AssertionError(f"{source} has no customer canary (`customer|graded on <name>,` or the `customer-platform` "
                         "generator's `b\"graded on <name>, `) to build the leaking text from")


def repo_slug(url: str) -> str | None:
    """The repository path of a remote URL with a host, lower-cased, with `<transport>::`, `git+`, a user, a port, the
    slashes around it and a final `.git` taken off: `owner/repo`, or the whole path when it is no `owner/repo` name
    (`theorem-labs/x/../rocq` stays as written, as the check reads it: no repository name). None for a local path or a
    URL with no host part. Only for telling which repository a gh read names; scripts/github-repo-visibility decides
    what is GitHub."""
    u = url.split("::", 1)[-1]
    u = u[4:] if u.startswith("git+") else u
    m = re.match(r"^(?:[A-Za-z][A-Za-z0-9+.-]*://)?(?:[^@/]+@)?(\[[^]]*\]|[^/:\[\]]+)(?::\d*)?[:/](.+)$", u)
    if not m or u.startswith(("/", "./", "../", "file:")):
        return None
    return re.sub(r"\.git$", "", m.group(2).strip("/"), flags=re.I).lower()


# leak-check-v8's public_leak_check.py (the sha256 devbox/leak-check-tag.env pins for it). Two of the gate's tests fail
# against it and pass against v4: v8 regressions, reported to ROCQ 2026-11-05 22:3xZ, to be fixed in the check, not
# here. Against v8 they are expected failures, so a v8 that passes them is an unexpected success and fails the run;
# against any other check they run, and must pass, in full.
V8_CHECK_SHA256 = "4a39983e891646e715abea1784af71c42101e8fb4f7d3f28745ab6552088b2bc"


def installed_check_is_v8() -> bool:
    try:
        check = leak_check()
    except unittest.SkipTest:
        return False
    py = check.resolve().parent / "public_leak_check.py"
    return py.is_file() and hashlib.sha256(py.read_bytes()).hexdigest() == V8_CHECK_SHA256


def v8_regression(test):
    """Marks TEST an expected failure when the check this run uses is leak-check-v8 (v8 regression, reported to ROCQ
    2026-11-05 22:3xZ); leaves it as it is for every other check."""
    return unittest.expectedFailure(test) if installed_check_is_v8() else test


# The hooks dispatcher of dispatch://BET-706 R6, from a leak-check tag: `install-global-hooks.sh <dir>` builds
# <dir>/global/ (one entry per git hook name, each running the dispatcher) from the global-hooks/ beside it, and writes
# <dir>/global.gitconfig, which sets core.hooksPath to it, last. The dotfiles' .gitconfig includes that file.
GLOBAL_HOOKS = Path(".local/share/public-leak-check-hooks")
DOTFILES_GITCONFIG = SHIMS.parent / ".gitconfig"


def global_hooks_source() -> Path:
    """The directory holding the tag's install-global-hooks.sh and global-hooks/ (and install-prepush-hook.sh): the one
    LEAK_CHECK_HOOKS_SOURCE names (a checkout's leak-check/), else the one devbox/sami-devbox-provision installs them in."""
    for c in (os.environ.get("LEAK_CHECK_HOOKS_SOURCE"), Path.home() / GLOBAL_HOOKS):
        if c and (Path(c) / "install-global-hooks.sh").is_file() and (Path(c) / "global-hooks" / "dispatch").is_file():
            return Path(c)
    raise unittest.SkipTest("no install-global-hooks.sh with its global-hooks/ on this machine (set LEAK_CHECK_HOOKS_SOURCE)")


def install_global_hooks(where: Path, env: dict[str, str]) -> Path:
    """The dispatcher installed in WHERE by the tag's own installer, as provisioning runs it; returns its global.gitconfig."""
    where.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([str(global_hooks_source() / "install-global-hooks.sh"), str(where)], env=env, capture_output=True, text=True, check=False)
    if r.returncode != 0 or not (where / "global.gitconfig").is_file():
        raise AssertionError(f"install-global-hooks.sh {where}: exit {r.returncode}\n{r.stdout}{r.stderr}")
    return where / "global.gitconfig"


def dotfiles_include() -> str:
    """The `[include]` stanza of the dotfiles' .gitconfig that names the dispatcher's global.gitconfig, as written."""
    m = re.search(r"^\[include\]\n[ \t]+path = ~/\.local/share/public-leak-check-hooks/global\.gitconfig\n",
                  DOTFILES_GITCONFIG.read_text(encoding="utf-8"), re.M)
    if not m:
        raise AssertionError(f"{DOTFILES_GITCONFIG} has no [include] of ~/{GLOBAL_HOOKS}/global.gitconfig")
    return m.group(0)


# Every git call is recorded, then goes to /usr/bin/git unchanged.
RECORD_GIT = '#!/bin/bash\nprintf \'%s\\n\' "$*" >> "@CALLS@"\nexec /usr/bin/git "$@"\n'

# A remote helper with the `connect` capability: git runs `git-remote-sink <remote> <url>` for a remote whose vcs is
# `sink`, and the helper hands git-receive-pack or git-upload-pack of a local bare repository named after the URL, with
# none of the client's repository or command-line config, as the far side of a real transport has.
SERVER_SIDE = ("env -u GIT_CONFIG_PARAMETERS -u GIT_CONFIG_COUNT -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE -u GIT_OBJECT_DIRECTORY"
               " -u GIT_ALTERNATE_OBJECT_DIRECTORIES -u GIT_COMMON_DIR -u GIT_NAMESPACE /usr/bin/git")
SINK_HELPER = r"""#!/bin/bash
key=$(printf '%s' "$2" | sha1sum | cut -c1-16)
sink="@SINKS@/$key.git"
[[ -d "$sink" ]] || /usr/bin/git init -q --bare "$sink"
printf '%s\t%s\n' "$2" "$sink" >> "@SINKS@.log"
while IFS= read -r line; do
    case "$line" in
        capabilities) printf 'connect\n\n' ;;
        "connect git-receive-pack" | "connect git-upload-pack") printf '\n'; exec @SERVER@ "${line#connect git-}" "$sink" ;;
        "") exit 0 ;;
        *) echo "git-remote-sink: unexpected '$line'" >&2; exit 1 ;;
    esac
done
""".replace("@SERVER@", SERVER_SIDE)

# A local-only stand-in for ssh, as GIT_SSH_COMMAND with GIT_SSH_VARIANT=ssh: git runs it as `[options] <host> <command>`
# and it runs that git-receive-pack or git-upload-pack on a local bare repository named after host and path, with the
# server side's clean environment. It opens no connection.
SSH_ADAPTER = r"""#!/bin/bash
printf '%s\n' "$*" >> "@SSH@.log"
host="${@: -2:1}" cmd="${@: -1}"
prog="${cmd%% *}"
eval "path=${cmd#* }" # git quotes the path as sq_quote_buf does
case "$prog" in git-receive-pack | git-upload-pack) ;; *) exit 91 ;; esac
sink="@SSH@/$(printf '%s:%s' "$host" "$path" | sha1sum | cut -c1-16).git"
[[ -d "$sink" ]] || /usr/bin/git init -q --bare "$sink"
exec @SERVER@ "${prog#git-}" "$sink"
""".replace("@SERVER@", SERVER_SIDE)

# Answers no gh call, and records each one: the check, and the gate through scripts/github-repo-visibility, read a
# repository's visibility through gh when the cache has no fresh answer, and no test may send that read to GitHub.
# Each line is the call's arguments, then (after \x1e) the arguments of the process that ran it, \x1f-separated, so a
# read can be traced to the check run for one push and the URL it was given (gh_calls() returns the arguments alone).
FAKE_GH = ('#!/bin/bash\nprintf \'%s\\036%s\\n\' "$*" "$(tr \'\\000\' \'\\037\' < /proc/$PPID/cmdline 2> /dev/null)" >> "@GHLOG@"\n'
           'exit 1\n')

# ssh for `ssh -G` alias lookups: the real ssh with only the test's config, so the user's and the system's configs
# play no part. Aliases resolving to github.com: three as the reviewer staged them (lanes/jj-gate-review-xf-2026-11-02.md,
# B2): a plain one, an upper-case Host (ssh matches it as written), a Match on the user; and two holding # and ?
# (the revision-2 review's R2-5).
FAKE_SSH = '#!/bin/bash\nexec "@SSH@" -F "@CFG@" "$@"\n'
SSH_CONFIG = ('Host alias-xf UPPERALIAS "alias#xf" "alias?xf"\n  HostName github.com\n'
              "Match host useralias user git\n  HostName github.com\n"
              # A config that chooses the host by the port git passes (`ssh -p PORT`): github.com only on 2222.
              'Match host portalias exec "test %p = 2222"\n  HostName github.com\n'
              # An alias whose HostName is the fully qualified github.com. (one trailing dot).
              "Host dotalias\n  HostName github.com.\n")

# The leak check's visibility cache as each test starts: the six theorem-labs repositories this box's cache records as
# public (2026-11-01), another public one, a private one, and public repositories of other owners. The check and the
# gate decide every GitHub target from these entries, each read lower-cased, and from the fake gh when an entry is
# missing or stale. The %72ocq entry is no repository name for either: the gate guards theorem-labs/%72ocq as
# unknown, and the check refuses a push to it unread (test_other_names_and_url_forms).
CACHE = {
    **{f"theorem-labs_{r}": "public" for r in ("rocq", "rocq-linux-process-semantics", "oss-verify", "metarocq", "legion", "oss-verify-proofs")},
    "theorem-labs_cached-public": "public",
    "theorem-labs_%72ocq": "public",
    "theorem-labs_private-fixture": "private",
    # A private repository of another owner: the check scans a push to any owner but theorem-labs as public, so only
    # the gate's visibility rule leaves a checkout of it alone.
    "sjawhar_private-fixture": "private",
    # sjawhar/dotfiles is public since its scrub (dispatch://INFRA-974): checked like any other public target.
    "sjawhar_dotfiles": "public",
    "sjawhar_rocq": "public",
    "sjawhar_legion": "public",
    "rocq-prover_rocq": "public",
}

# Guarded or not, under CACHE, the fake gh (no answer, so unknown) and SSH_CONFIG, by the rule of dispatch://BET-706
# (the plan's shared contract): a URL is GUARD when it names GitHub (github.com, www.github.com or ssh.github.com, directly or through
# an ssh alias) and its repository is public or of unknown visibility, or when its host part cannot be parsed; NO when
# the repository is private or internal, or the URL is a local path or names another host. A checkout is guarded when
# any of its URLs is GUARD. (want, remote name, URL, how the config holds it: "" as `git remote add` writes it,
# "quoted" with the URL in double quotes, "insteadOf" for a URL that url.https://github.com/theorem-labs/.insteadOf=r3:
# rewrites, as in the revision-3 review's R3-2, "also <URL>" for a second remote, `zzz`, at <URL>, "sshCommand" for
# a checkout whose core.sshCommand is set, or "partial" for a partial clone's remote (promisor, filter blob:none), whose
# fetch line git prints as `<URL> (fetch) [blob:none]`.) Each row is judged with neither GIT_SSH_COMMAND nor GIT_SSH set. A comment
# "was N" or "was Y" marks rows whose answer BET-706 changed from the old rule (a fixed list of names matched in
# `git remote -v`).
GUARD_TABLE = [
    ("Y", "origin", "https://github.com/theorem-labs/rocq.git", ""),
    ("Y", "origin", "https://github.com/theorem-labs/rocq", ""),
    ("Y", "origin", "git@github.com:theorem-labs/rocq.git", ""),
    ("Y", "origin", "git@github.com:/theorem-labs/rocq.git", ""),
    ("Y", "origin", "ssh://git@ssh.github.com:443/theorem-labs/rocq.git", ""),
    ("Y", "origin", "https://x-access-token:not-a-secret@github.com/theorem-labs/rocq.git", ""),
    ("Y", "origin", "https://github.com//theorem-labs/rocq.git", ""),
    # git's <transport>::<address> form: the transport is stripped, and the address names github.com.
    ("Y", "origin", "https::https://github.com/theorem-labs/rocq", ""),
    # ssh aliases that SSH_CONFIG resolves to github.com.
    ("Y", "origin", "git@alias-xf:theorem-labs/rocq.git", ""),
    ("Y", "origin", "git@UPPERALIAS:theorem-labs/rocq.git", ""),
    ("Y", "origin", "git@useralias:theorem-labs/rocq", ""),
    # Was Y: without the user `git` the Match does not apply, and useralias names no GitHub host.
    ("N", "origin", "useralias:theorem-labs/rocq", ""),
    ("Y", "origin", "git@alias#xf:theorem-labs/rocq.git", ""),
    ("Y", "origin", "git@alias?xf:theorem-labs/rocq.git", ""),
    # Was Y, each: a path holding theorem-labs/rocq on a host that is not GitHub, or on none.
    ("N", "origin", "git@corp.example:mirror space/theorem-labs/rocq.git", ""),
    ("N", "origin", "/tmp/mirror space/theorem-labs/rocq.git", ""),
    ("N", "origin", "https://notgithub.com/theorem-labs/rocq.git", ""),
    ("N", "origin", "file:///srv/theorem-labs/rocq.git", ""),
    ("N", "origin", "https://example.com/not-theorem-labs/rocq.git", ""),
    ("N", "origin", "git@unknown-alias:theorem-labs/rocq.git", ""),
    # The revision-2 review's R2-2, now: a GitHub path with a space is no repository name, so unknown; read only up
    # to the space it would be the private repository.
    ("Y", "origin", "git@alias-xf:theorem-labs/private-fixture space.git", ""),
    ("Y", "origin", "git+ssh://git@github.com/theorem-labs/rocq-linux-process-semantics", ""),
    ("Y", "origin", "ssh+git://github.com/theorem-labs/oss-verify.git", ""),
    ("Y", "origin", "git@github.com:theorem-labs/oss-verify-proofs.git", ""),
    ("Y", "origin", "https://github.com/theorem-labs/legion", ""),
    ("Y", "origin", "https://github.com/theorem-labs/metarocq.git", ""),
    ("Y", "origin", "https://github.com/theorem-labs/cached-public.git", ""),
    ("Y", "origin", "https://github.com/theorem-labs/%72ocq.git", ""),
    # Letter case and a trailing slash.
    ("Y", "origin", "HTTPS://GitHub.COM/Theorem-Labs/Rocq.GIT", ""),
    ("Y", "origin", "Git@GitHub.com:Theorem-Labs/Rocq.git", ""),
    ("Y", "origin", "https://github.com/Theorem-Labs/Cached-Public.git", ""),
    ("Y", "origin", "https://github.com/theorem-labs/rocq/", ""),
    ("Y", "origin", "https://github.com/theorem-labs/rocq.git/", ""),
    # The revision-3 review's R3-2: a quoted URL, and one an insteadOf rewrites.
    ("Y", "origin", "https://github.com/theorem-labs/rocq.git", "quoted"),
    ("Y", "origin", "r3:rocq.git", "insteadOf"),
    # Was Y: a remote's name is not its URL, and this URL names another host.
    ("N", "theorem-labs/rocq", "https://example.com/elsewhere.git", ""),
    ("N", "origin", "https://github.com/theorem-labs/private-fixture.git", ""),
    # Was N, each: a GitHub repository whose visibility the cache does not hold and gh does not answer is unknown.
    ("Y", "origin", "https://github.com/theorem-labs/never-cached.git", ""),
    ("Y", "origin", "https://github.com/theorem-labs/oss-verify-proofs-internal.git", ""),
    ("Y", "origin", "https://github.com/sjawhar/dotfiles-public", ""),
    # Was N, each: a GitHub path that is no owner/repository name is unknown.
    ("Y", "origin", "https://github.com/theorem-labs/./rocq.git", ""),
    ("Y", "origin", "https://github.com/theorem-labs/x/../rocq.git", ""),
    ("Y", "origin", "https://github.com/theorem-labs///rocq.git", ""),
    ("Y", "origin", "https://github.com/theorem-labs/rocq.git?x=1", ""),
    ("Y", "origin", "https://github.com/theorem-labs/rocq//", ""),
    ("Y", "origin", "https://github.com/theorem-labs/rocq/extra", ""),
    ("Y", "origin", "https://github.com/theorem-labs/.git", ""),
    # Was N, each: public repositories of other owners, by their cache entries.
    ("Y", "origin", "https://github.com/sjawhar/rocq", ""),
    ("Y", "origin", "https://github.com/rocq-prover/rocq", ""),
    ("Y", "origin", "https://github.com/sjawhar/dotfiles.git", ""),
    ("Y", "origin", "git@github.com:SJawhar/Dotfiles.git", ""),
    ("Y", "origin", "https://github.com/sjawhar/dotfiles/", ""),
    # A host part that cannot be parsed: unknown.
    ("Y", "origin", "ssh:///theorem-labs/private-fixture.git", ""),
    ("Y", "origin", "git@:theorem-labs/private-fixture.git", ""),
    ("Y", "origin", "https://bad host/theorem-labs/private-fixture.git", ""),
    ("Y", "origin", "https://[github.com/theorem-labs/private-fixture.git", ""),
    # A host in brackets, which git takes out.
    ("Y", "origin", "git@[github.com]:theorem-labs/rocq.git", ""),
    ("Y", "origin", "ssh://git@[alias-xf]:22/theorem-labs/rocq.git", ""),
    ("N", "origin", "ssh://git@[github.com]:22/theorem-labs/private-fixture.git", ""),
    ("N", "origin", "https://[::1]/theorem-labs/rocq.git", ""),
    # A port-matching ssh config: git runs `ssh -p 2222 git@portalias` for the first, plain ssh for the others.
    ("Y", "origin", "ssh://git@portalias:2222/theorem-labs/rocq.git", ""),
    ("N", "origin", "ssh://git@portalias/theorem-labs/rocq.git", ""),
    ("N", "origin", "git@portalias:theorem-labs/rocq.git", ""),
    # www.github.com is GitHub too (git follows it to github.com; the check's own list, public-leak-check.sh).
    ("Y", "origin", "https://www.github.com/theorem-labs/rocq.git", ""),
    ("Y", "origin", "https://WWW.GitHub.com/sjawhar/rocq", ""),
    ("Y", "origin", "git@www.github.com:theorem-labs/rocq.git", ""),
    ("N", "origin", "https://www.github.com/theorem-labs/private-fixture.git", ""),
    # One trailing dot names the same host, in a URL and in what ssh -G answers.
    ("Y", "origin", "https://github.com./theorem-labs/rocq.git", ""),
    ("N", "origin", "https://GitHub.COM./theorem-labs/private-fixture.git", ""),
    ("Y", "origin", "git@dotalias:theorem-labs/rocq.git", ""),
    # The red team's finding 1: GitHub's ssh endpoint, fully qualified (the old name rule guarded it).
    ("Y", "origin", "ssh://git@ssh.github.com.:443/theorem-labs/rocq.git", ""),
    ("N", "origin", "ssh://git@ssh.github.com.:443/theorem-labs/private-fixture.git", ""),
    # The checkout's core.sshCommand: git runs that command, which may resolve an alias otherwise than a plain
    # `ssh -G` (its own -F, say), so an alias is unknown there; a literal GitHub host still decides by its repository.
    ("Y", "origin", "git@corp.example:theorem-labs/rocq.git", "sshCommand"),
    ("Y", "origin", "git@alias-xf:theorem-labs/private-fixture.git", "sshCommand"),
    ("N", "origin", "git@github.com:theorem-labs/private-fixture.git", "sshCommand"),
    ("N", "origin", "git@alias-xf:theorem-labs/private-fixture.git", ""),
    ("N", "origin", "../a.git", ""),
    ("N", "theorem-labs", "origin", ""),
    # Two remotes: one GUARD URL guards the checkout, whichever remote holds it.
    ("Y", "origin", "https://github.com/theorem-labs/private-fixture.git", "also https://github.com/sjawhar/rocq"),
    ("N", "origin", "https://github.com/theorem-labs/private-fixture.git", "also ../b.git"),
    # A partial clone: git prints its filter after the fetch line's annotation, which is not part of the URL. Was Y
    # (the filter read as part of the URL made it unknown) for the two private repositories.
    ("N", "origin", "https://github.com/theorem-labs/private-fixture.git", "partial"),
    ("N", "origin", "https://github.com/sjawhar/private-fixture.git", "partial"),
    ("Y", "origin", "https://github.com/theorem-labs/rocq.git", "partial"),
    ("Y", "origin", "git@alias-xf:theorem-labs/private-fixture space.git", "partial"),
    ("N", "origin", "git@corp.example:theorem-labs/rocq.git", "partial"),
    # A filter may hold ` [`, `]` and ` (fetch)` itself (git accepts `sparse:oid=<blob-ish>` naming any path, as
    # Bet2211RedTeam showed with a real clone): a fetch line's URL ends at its first ` (fetch)`.
    ("N", "origin", "https://github.com/theorem-labs/private-fixture.git", "partial sparse:oid=HEAD:sparse [x]"),
    ("N", "origin", "https://github.com/sjawhar/private-fixture.git", "partial sparse:oid=HEAD:a (fetch) [b]"),
    ("Y", "origin", "https://github.com/theorem-labs/rocq.git", "partial sparse:oid=HEAD:sparse [x]"),
    # A filter holding a newline (a sparse-pattern path with one; Bet2211XF's real clone): git prints the rest of the
    # filter on a line of its own, with no tab, which is no remote's line.
    ("N", "origin", "https://github.com/theorem-labs/private-fixture.git", "partial sparse:oid=HEAD:sparse\n:repo"),
    ("Y", "origin", "https://github.com/theorem-labs/rocq.git", "partial sparse:oid=HEAD:sparse\n:repo"),
]


class JjPushLeakGateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.check = leak_check()
        self.customer = customer_name(self.check)
        self.leak_text = f"(* measured on {self.customer} *)"
        fake = self.tmp / "fake"
        self.ghdir = self.tmp / "fake-gh"
        for d in (fake, self.ghdir):
            d.mkdir()
        self.calls = self.tmp / "calls.log"
        self.ghlog = self.tmp / "gh.log"
        self.sinks = self.tmp / "sinks"
        self.sinks.mkdir()
        (fake / "git").write_text(RECORD_GIT.replace("@CALLS@", str(self.calls)), encoding="utf-8")
        (fake / "git-remote-sink").write_text(SINK_HELPER.replace("@SINKS@", str(self.sinks)), encoding="utf-8")
        (self.ghdir / "gh").write_text(FAKE_GH.replace("@GHLOG@", str(self.ghlog)), encoding="utf-8")
        real_ssh = shutil.which("ssh")
        if not real_ssh:
            raise unittest.SkipTest("no ssh on this machine to resolve ssh aliases with")
        self.ssh_config = self.tmp / "sshconfig"
        self.ssh_config.write_text(SSH_CONFIG, encoding="utf-8")
        (self.ghdir / "ssh").write_text(FAKE_SSH.replace("@SSH@", real_ssh).replace("@CFG@", str(self.ssh_config)), encoding="utf-8")
        for f in (*fake.iterdir(), self.ghdir / "gh", self.ghdir / "ssh"):
            f.chmod(0o755)
        # The gate (scripts/github-repo-visibility) and the check read the cache where XDG_CACHE_HOME says, and the
        # sweep's old rule read ~/.cache/public-leak-check: one directory here, as on this box.
        self.home = self.tmp / "home"
        self.cache = self.home / ".cache" / "public-leak-check"
        self.cache.mkdir(parents=True)
        for name, word in CACHE.items():
            (self.cache / name).write_text(word, encoding="utf-8")
        # shims/gh, which scripts/github-repo-visibility runs by path, in an agent session: its agent config directory,
        # no knives, no read cache and no call log, and a token that is not one, so it hands the call straight to the
        # fake gh.
        gh_config = self.home / ".config" / "gh-agent"
        gh_config.mkdir(parents=True)
        (gh_config / "hosts.yml").touch()
        (gh_config / "config.yml").touch()
        cfg = self.tmp / "jj.toml"
        cfg.write_text('[user]\nname = "T"\nemail = "t@example.com"\n[signing]\nbehavior = "drop"\n', encoding="utf-8")
        self.fake = fake
        self.env = {
            **os.environ,
            # The fake gh first, ahead of this shims dir, whose dotfiles checkout holds shims/gh: no gh past it is
            # reached. Then this shims dir, then the recording git and the sink helper.
            "PATH": os.pathsep.join([str(self.ghdir), str(SHIMS), str(fake), str(real_jj().parent), os.environ["PATH"]]),
            "HOME": str(self.home),
            "JJ_CONFIG": str(cfg),
            "XDG_CACHE_HOME": str(self.home / ".cache"),
            "PUBLIC_LEAK_CHECK": str(self.check),
            "OMP_SESSION_ID": "00000000-0000-0000-0000-000000000000",
            "GIT_CEILING_DIRECTORIES": str(self.tmp),
            "GIT_ALLOW_PROTOCOL": "sink",
            "GIT_SSH_COMMAND": "false",
            "DOTFILES_DIR": str(SHIMS.parent),
            "GH_READ_CACHE": "0",
            "GH_READ_CACHE_LOG": "0",
            "KNIVES_GH_BYPASS": "1",
            "GH_TOKEN": "fixture-not-a-secret",
        }
        self.assertEqual(shutil.which("ssh", path=self.env["PATH"]), str(self.ghdir / "ssh"), "an ssh other than the fake one comes first")
        self.assertEqual(shutil.which("gh", path=self.env["PATH"]), str(self.ghdir / "gh"), "a gh other than the fake one comes first")
        self.env.pop("DOTFILES_JJ_SHIM", None)
        self.repo = self.tmp / "w"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("-c", "user.name=T", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "init")
        self.init = self.git("rev-parse", "HEAD")
        self.remote("origin", ROCQ)
        self.ok("git", "init", "--colocate")

    def tearDown(self):
        subprocess.run(["chmod", "-R", "u+rwX", str(self.tmp)], check=False, capture_output=True)
        shutil.rmtree(self.tmp)

    def git(self, *args: str) -> str:
        return subprocess.run(["/usr/bin/git", *args], cwd=self.repo, env=self.env, check=True, capture_output=True, text=True).stdout.strip()

    def remote(self, name: str, url: str):
        self.git("remote", "add", name, url)
        self.git("config", f"remote.{name}.vcs", "sink")

    def keep_cache_fresh(self):
        """Every cache entry written now, as far as the check's clock goes. The check trusts a cached "private" for 60 s
        and a "public" for 600 s from the entry's mtime, then asks gh, here the fake one, which answers nothing: a test
        slower than that on a loaded machine read its private target as unknown and saw it refused. Only the mtime moves;
        what a test did to an entry, its directory or its permissions stays."""
        try:
            entries = list(self.cache.iterdir())
        except OSError:
            return
        for entry in entries:
            try:
                os.utime(entry)
            except OSError:
                pass

    def jj(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        self.keep_cache_fresh()
        # These cases target shims/git, independently of the stronger jj entry gate.
        return run_bounded([getattr(self, "jj_binary", str(real_jj())), *args], cwd or self.repo, self.env)

    def ok(self, *args: str) -> str:
        r = self.jj(*args)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def commit(self, path: str, text: str, message: str, bookmark: str | None = None) -> str:
        (self.repo / path).write_text(text + "\n", encoding="utf-8")
        self.ok("commit", "-m", message)
        self.assertIn(path, self.ok("diff", "-r", "@-", "--name-only"), "the commit did not take the file")
        if bookmark:
            self.ok("bookmark", "set", "--allow-backwards", bookmark, "-r", "@-")
            self.assertIn(bookmark, self.ok("bookmark", "list", bookmark))
        return self.ok("log", "--no-graph", "-r", "@-", "-T", "commit_id").strip()

    def leak(self, bookmark: str | None = "b1", where: str = "content", path: str = "guard.v") -> str:
        if where == "content":
            return self.commit(path, self.leak_text, "Add the guard test", bookmark)
        return self.commit(path, "(* clean *)", f"Port the guard check graded on {self.customer}", bookmark)

    def received(self, commit: str) -> list[str]:
        """The URLs whose sink holds COMMIT."""
        log = Path(f"{self.sinks}.log")
        sinks = dict(ln.split("\t") for ln in log.read_text(encoding="utf-8").splitlines()) if log.exists() else {}
        return sorted(url for url, sink in sinks.items()
                      if subprocess.run(["/usr/bin/git", "--git-dir", sink, "cat-file", "-e", commit], capture_output=True).returncode == 0)

    def gh_calls(self) -> list[str]:
        return [ln.split("\x1e", 1)[0] for ln in self.gh_log()]

    def gh_log(self) -> list[str]:
        # split("\n"), not splitlines(): splitlines() also breaks at the \x1e this log puts between call and caller.
        return [ln for ln in self.ghlog.read_text(encoding="utf-8").split("\n") if ln] if self.ghlog.exists() else []

    def unexpected_gh_calls(self) -> list[str]:
        """Every gh call so far but one kind: the check's own read of the visibility of ANOTHER remote of this
        checkout, made while it checked a push to a destination that read does not name. The check counts what another
        public remote already holds as not new to the destination. v4 assumed every non-theorem-labs GitHub repository
        was public; v8 reads it (fresh, past the cache, for theorem-labs remotes too, and widens nothing when the read
        fails), so v8 scans more (ROCQ, 2026-11-05 22:29Z; v4 public-leak-check.sh:370-376, scope_of). A read of the
        destination itself, which its fresh cache entry must answer, every read the gate makes
        (scripts/github-repo-visibility's `api repos/...`), a call no check run made, and any other call stay
        unexpected."""
        # Each URL as the gate reads `git remote -v`: a push line without its final ` (push)`, a fetch line up to its
        # first ` (fetch)` (a partial clone's filter follows it).
        urls = [u[: -len(" (push)")] if u.endswith(" (push)") else u.split(" (fetch)", 1)[0]
                for u in (ln.split("\t", 1)[1] for ln in self.git("remote", "-v").splitlines() if "\t" in ln)]
        remotes = {repo_slug(u) for u in urls} - {None}
        out = []
        for ln in self.gh_log():
            call, _, parent = ln.partition("\x1e")
            argv = parent.rstrip("\x1f").split("\x1f") if parent else []
            m = re.fullmatch(r"api --hostname github\.com repos/(\S+) --jq \.visibility", call)
            # The check run's own arguments end with the URL it was given: `<check> <remote> <url>`.
            by_check = len(argv) >= 2 and any(a.endswith(("/public_leak_check.py", "/public-leak-check.sh")) for a in argv)
            if m and by_check and m.group(1).lower() in remotes and m.group(1).lower() != repo_slug(argv[-1]):
                continue
            out.append(call)
        return out

    def assert_refused(self, r: subprocess.CompletedProcess[str], commit: str, url: str = ROCQ, gh: bool = False):
        """Refused by the check, with the commit and the matching text in lines jj shows even under --quiet, and
        nothing of COMMIT received at URL; unless GH says a read was due, gh got no call but the check's reads of the
        checkout's other remotes (unexpected_gh_calls), and no read of the destination, whose cache entry is fresh."""
        self.assertNotEqual(r.returncode, 0, r.stderr)
        err = re.sub(r"\x1b\[[0-9;]*m", "", r.stderr)
        self.assertIn("LEAK CHECK: refusing to push", err)
        self.assertIn(f"error: git shim: the public leak check refused this jj push to {url}", err)
        shown = [ln for ln in err.splitlines() if ln.startswith("error: ")]
        self.assertTrue([ln for ln in shown if commit[:7] in ln], ("the refusal must name the commit", r.stderr))
        self.assertTrue([ln for ln in shown if f"[customer: {self.customer}]" in ln], ("the refusal must name what matched", r.stderr))
        self.assertNotIn(url, self.received(commit))
        if not gh:
            self.assertEqual(self.unexpected_gh_calls(), [])

    def assert_refused_unreadable(self, r: subprocess.CompletedProcess[str], commit: str, url: str, slug: str):
        """Refused by the check because it cannot read the visibility of SLUG, the target at URL; nothing of COMMIT
        received there, and no leaked text named."""
        self.assertNotEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"error: LEAK CHECK: cannot read the visibility of {slug}", r.stderr)
        self.assertIn(f"error: git shim: the public leak check refused this jj push to {url}", r.stderr)
        self.assertNotIn("Take out", r.stderr)
        self.assertNotIn(url, self.received(commit))

    def assert_refused_unread(self, r: subprocess.CompletedProcess[str], commit: str, why: str):
        """Refused before any content was read, saying so and that the commits are not the problem, and sent nowhere."""
        self.assertNotEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"error: git shim: {why}", r.stderr)
        self.assertIn("nothing is wrong with your commits: your commits were not read", r.stderr)
        self.assertNotIn("LEAK CHECK", r.stderr)
        self.assertEqual(self.received(commit), [])

    def assert_pushed(self, r: subprocess.CompletedProcess[str], commit: str, url: str = ROCQ):
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(url, self.received(commit), r.stderr)

    def path_with(self, entry: str | Path, path: str | None = None) -> str:
        """PATH (or self.env's) with ENTRY where this shims dir was, every other entry where it was."""
        return os.pathsep.join(str(entry) if p == str(SHIMS) else p for p in (path or self.env["PATH"]).split(os.pathsep))

    def git_wrapper(self, name: str, body: str, shim: Path = SHIMS / "git") -> Path:
        """A git for jj's git.executable-path that execs shims/git (or SHIM), so it still has jj as its parent."""
        w = self.tmp / name
        w.write_text(f"#!/bin/bash\n{body}\nexec \"{shim}\" \"${{args[@]}}\"\n", encoding="utf-8")
        w.chmod(0o755)
        return w

    def sink_of(self, url: str) -> Path:
        """The bare repository git-remote-sink hands git for URL."""
        return self.sinks / f"{hashlib.sha1(url.encode()).hexdigest()[:16]}.git"

    def use_ssh(self):
        """git's own ssh transport, through SSH_ADAPTER, and its own file transport."""
        d = self.tmp / "ssh"
        d.mkdir(exist_ok=True)
        adapter = self.tmp / "ssh-adapter"
        adapter.write_text(SSH_ADAPTER.replace("@SSH@", str(d)), encoding="utf-8")
        adapter.chmod(0o755)
        self.env.update({"GIT_SSH_COMMAND": str(adapter), "GIT_SSH_VARIANT": "ssh", "GIT_ALLOW_PROTOCOL": "sink:file:ssh"})

    def ssh_sink(self, host: str, path: str) -> Path:
        """The bare repository SSH_ADAPTER hands git for HOST and PATH."""
        return self.tmp / "ssh" / f"{hashlib.sha1(f'{host}:{path}'.encode()).hexdigest()[:16]}.git"

    @staticmethod
    def holds(repo: Path, commit: str) -> bool:
        return subprocess.run(["/usr/bin/git", "--git-dir", str(repo), "cat-file", "-e", commit], capture_output=True).returncode == 0

    def install_git_hook(self):
        """Installs the pre-push hook install-prepush-hook.sh installs, so a plain git push gives the verdict to match."""
        installer = self.check.parent / "install-prepush-hook.sh"
        if not installer.is_file():
            self.skipTest("no install-prepush-hook.sh beside the check")
        self.env["XDG_CONFIG_HOME"] = str(self.tmp / "config")
        subprocess.run([str(installer), str(self.repo)], cwd=self.tmp, env=self.env, check=True, capture_output=True)
        self.assertIn("PUBLIC-LEAK-CHECK-WRAPPER", (self.repo / ".git" / "hooks" / "pre-push").read_text(encoding="utf-8"))

    def git_push(self, remote: str, commit: str, ref: str) -> subprocess.CompletedProcess[str]:
        """A plain `git push` through shims/git, which leaves it to the checkout's own hook."""
        self.keep_cache_fresh()
        return subprocess.run(["git", "push", remote, f"{commit}:refs/heads/{ref}"], cwd=self.repo, env=self.env, capture_output=True, text=True)

    def test_only_the_checks_reads_of_other_remotes_are_allowed(self):
        # What assert_refused lets through, on a hand-written gh log: only the check's read of another remote of this
        # checkout during a push to a destination it does not name.
        self.remote("other", PRIVATE)
        check = "/usr/bin/python3\x1f-I\x1f/x/public_leak_check.py\x1forigin\x1f"
        read = "api --hostname github.com repos/{} --jq .visibility"
        lines = {
            "allowed: another remote": (read.format("theorem-labs/private-fixture"), check + ROCQ),
            "the destination itself": (read.format("theorem-labs/rocq"), check + ROCQ),
            "the destination, mixed case": (read.format("Theorem-Labs/Rocq"), check + ROCQ),
            "a repository no remote names": (read.format("theorem-labs/elsewhere"), check + ROCQ),
            "the gate's read": ("api repos/theorem-labs/private-fixture --jq .visibility", check + ROCQ),
            "not made by a check": (read.format("theorem-labs/private-fixture"), "bash\x1f/x/shims/gh\x1fapi"),
        }
        self.ghlog.write_text("".join(f"{call}\x1e{parent}\n" for call, parent in lines.values()), encoding="utf-8")
        self.assertEqual(self.unexpected_gh_calls(), [call for k, (call, _) in lines.items() if not k.startswith("allowed")])

    # Every way of naming what to push: jj resolves them all before it runs git.
    def test_bookmark_content(self):
        c = self.leak()
        self.assert_refused(self.jj("git", "push", "--bookmark", "b1"), c)

    def test_short_bookmark_message(self):
        c = self.leak(where="message")
        self.assert_refused(self.jj("git", "push", "-b", "b1"), c)

    def test_named(self):
        c = self.leak(None)
        self.assert_refused(self.jj("git", "push", "--named", "newbm=@-"), c)

    def test_change(self):
        c = self.leak(None)
        self.assert_refused(self.jj("git", "push", "--change", "@-"), c)

    def test_revision(self):
        c = self.leak()
        self.ok("bookmark", "track", "b1@origin")
        self.assert_refused(self.jj("git", "push", "-r", "@-"), c)

    def test_all_and_tracked(self):
        c = self.leak()
        self.assert_refused(self.jj("git", "push", "--all"), c)
        self.ok("bookmark", "track", "b1@origin")
        self.assert_refused(self.jj("git", "push", "--tracked"), c)

    def test_tag(self):
        c = self.leak(None)
        self.ok("tag", "set", "v1", "-r", "@-")
        self.assert_refused(self.jj("git", "push", "--tag", "v1"), c)

    def test_global_options_anywhere(self):
        c = self.leak()
        self.assert_refused(self.jj("git", "--color", "never", "push", "-b", "b1"), c)
        self.assert_refused(self.jj("--color", "always", "-R", str(self.repo), "git", "push", "--quiet", "-b", "b1"), c)

    def test_quiet_still_names_the_commit_and_the_match(self):
        # jj drops a git's plain stderr under --quiet, and all of it when git says it "failed to push some refs".
        c = self.leak()
        for argv in (["--quiet", "git", "push", "-b", "b1"], ["--config", "ui.quiet=true", "git", "push", "-b", "b1"]):
            r = self.jj(*argv)
            self.assert_refused(r, c)
            self.assertNotIn("unfamiliar", r.stderr)

    def test_aliases(self):
        c = self.leak()
        self.ok("config", "set", "--repo", "aliases.pub", '["git", "push"]')
        self.ok("config", "set", "--repo", "aliases.send", '["pub"]')
        self.ok("config", "set", "--repo", "aliases.ci", '["--color", "never", "git", "push"]')
        for alias in ("pub", "send", "ci"):
            self.assert_refused(self.jj(alias, "-b", "b1"), c)

    def test_a_push_option_jj_may_add_changes_nothing(self):
        # git, not this gate, reads the push's options: a later jj adding one (here --thin) is checked as before.
        w = self.git_wrapper("git-with-thin", 'args=(); for a in "$@"; do args+=("$a"); [[ "$a" == push ]] && args+=(--thin); done')
        clean = self.commit("guard2.v", "(* clean *)", "Clean", "b2")
        c = self.leak()
        self.assert_refused(self.jj("--config", f"git.executable-path={w}", "git", "push", "-b", "b1"), c)
        self.assert_pushed(self.jj("--config", f"git.executable-path={w}", "git", "push", "-b", "b2"), clean)
        self.assertTrue([ln for ln in self.calls.read_text(encoding="utf-8").splitlines() if "--thin" in ln], "the wrapper did not add --thin")

    # Every remote and every push URL: git hands the hook the URL it pushes to.
    def test_second_remote(self):
        self.remote("aaa", PRIVATE)
        c = self.leak()
        for remote in ("aaa", "origin"):
            self.ok("bookmark", "track", f"b1@{remote}")
        r = self.jj("git", "push", "--remote", "aaa", "--remote", "origin", "-b", "b1")
        self.assert_refused(r, c)
        self.assertEqual(self.received(c), [PRIVATE], "the private remote takes the push, the public one does not")

    def test_second_push_url(self):
        # git pushes to each push URL in turn and runs the hook for each.
        self.git("config", "--add", "remote.origin.pushurl", ROCQ)
        self.git("config", "--add", "remote.origin.pushurl", PRIVATE)
        c = self.leak()
        self.assert_refused(self.jj("git", "push", "-b", "b1"), c)
        self.assertEqual(self.received(c), [PRIVATE])

    def test_a_private_push_url_first_does_not_hide_the_commit(self):
        # After git pushed to the private URL it moves refs/remotes/origin/b1 to the commit. The check once counted
        # every commit reachable from origin/* as already on the target and so scanned 0 commits for the public URL;
        # it now leaves out only what the target itself holds (git ls-remote), so the public URL is still refused.
        self.git("config", "--add", "remote.origin.pushurl", PRIVATE)
        self.git("config", "--add", "remote.origin.pushurl", ROCQ)
        c = self.leak()
        self.assert_refused(self.jj("git", "push", "-b", "b1"), c)

    def test_other_names_and_url_forms(self):
        self.remote("mirror", "git@github.com:Theorem-Labs/Rocq.git")
        self.remote("work", str(self.tmp / "private-mirror.git"))
        self.git("config", "remote.work.pushurl", "ssh://git@ssh.github.com:443/theorem-labs/metarocq.git")
        self.git("config", "url.https://github.com/.insteadOf", "gh:")
        self.remote("short", "gh:theorem-labs/rocq")
        self.git("config", "url.https://github.com/theorem-labs/.pushInsteadOf", str(self.tmp / "pi") + "/")
        self.remote("pi", str(self.tmp / "pi" / "rocq.git"))
        self.remote("dots", "https://GitHub.com./theorem-labs/x/../rocq.git/")
        self.remote("pct", "https://github.com/theorem-labs/%72ocq")
        c = self.leak()
        urls = {"mirror": "git@github.com:Theorem-Labs/Rocq.git", "work": "ssh://git@ssh.github.com:443/theorem-labs/metarocq.git",
                "short": "https://github.com/theorem-labs/rocq", "pi": "https://github.com/theorem-labs/rocq.git",
                "dots": "https://GitHub.com./theorem-labs/x/../rocq.git/"}
        for remote, url in urls.items():
            self.ok("bookmark", "track", f"b1@{remote}")
            self.assert_refused(self.jj("git", "push", "--remote", remote, "-b", "b1"), c, url)
        # The gate guards the percent-encoded path; the check takes theorem-labs/%72ocq for no repository name, so it
        # cannot read its visibility and refuses before it scans.
        pct = "https://github.com/theorem-labs/%72ocq"
        self.ok("bookmark", "track", "b1@pct")
        self.assert_refused_unreadable(self.jj("git", "push", "--remote", "pct", "-b", "b1"), c, pct, "theorem-labs/%72ocq")
        self.assertEqual(self.received(c), [])

    def test_ssh_aliases_are_guarded(self):
        # The reviewer's three aliases (lanes/jj-gate-review-xf-2026-11-02.md, B2), each the checkout's only remote:
        # SSH_CONFIG resolves each to github.com (`ssh -G`, with the URL's user, so the Match on the user applies), and
        # theorem-labs/rocq there is public, so the checkout is guarded.
        c = self.leak()
        for url in ("git@alias-xf:theorem-labs/rocq.git", "git@UPPERALIAS:theorem-labs/rocq.git", "git@useralias:theorem-labs/rocq.git"):
            self.git("remote", "set-url", "origin", url)
            r = self.jj("git", "push", "-b", "b1")
            self.assert_refused(r, c, url)
        self.assertEqual(self.received(c), [])

    def guard_checkouts(self, env: dict[str, str]) -> list[str]:
        """One real checkout per GUARD_TABLE row, under $TMP/guard."""
        checkouts = []
        for k, (_, name, url, how) in enumerate(GUARD_TABLE):
            co = self.tmp / "guard" / f"{k:02d}"
            subprocess.run(["/usr/bin/git", "init", "-q", str(co)], env=env, check=True)
            subprocess.run(["/usr/bin/git", "-C", str(co), "remote", "add", name, url], env=env, check=True)
            if how == "quoted":
                cfg = co / ".git" / "config"
                text = cfg.read_text(encoding="utf-8")
                self.assertIn(f"\turl = {url}\n", text)
                cfg.write_text(text.replace(f"\turl = {url}\n", f'\turl = "{url}"\n'), encoding="utf-8")
            elif how == "insteadOf":
                subprocess.run(["/usr/bin/git", "-C", str(co), "config", "url.https://github.com/theorem-labs/.insteadOf", "r3:"], env=env, check=True)
            elif how.startswith("also "):
                subprocess.run(["/usr/bin/git", "-C", str(co), "remote", "add", "zzz", how.removeprefix("also ")], env=env, check=True)
            elif how == "sshCommand":
                subprocess.run(["/usr/bin/git", "-C", str(co), "config", "core.sshCommand", "ssh -F /nonexistent"], env=env, check=True)
            elif how == "partial" or how.startswith("partial "):
                self.make_partial(co, name, env, how.removeprefix("partial").strip() or "blob:none")
            checkouts.append(str(co))
        return checkouts

    def make_partial(self, co: Path, name: str, env: dict[str, str], spec: str = "blob:none") -> None:
        """Remote NAME of CO configured as a partial clone's with filter SPEC (what `git clone --filter=SPEC` writes), and
        `git remote -v` printing that filter after its fetch line, as these rows need."""
        for key, value in (("promisor", "true"), ("partialclonefilter", spec)):
            subprocess.run(["/usr/bin/git", "-C", str(co), "config", f"remote.{name}.{key}", value], env=env, check=True)
        listing = subprocess.run(["/usr/bin/git", "-C", str(co), "remote", "-v"], env=env, check=True, capture_output=True, text=True).stdout
        self.assertIn(f" (fetch) [{spec}]\n", listing, "git prints no partial-clone filter here")

    def test_guarded_is_the_sweepers_rule(self):
        # dispatch://BET-706, the plan's shared contract: the gate (through scripts/github-repo-visibility) and the hook
        # sweep (sweep-hooks.sh, in theorem-labs/agent-infra, which cannot call a dotfiles script) each decide by a
        # checkout's remote URLs and their visibility, with readers of their own. Each GUARD_TABLE row is a real
        # checkout. The gate's own functions judge its `git remote -v` and must give the table's answer. When
        # sweep-hooks.sh is beside the check, its own code, loaded as its three callers load it (everything above
        # `i=0`), with the same cache, fake gh and fake ssh, must give the same answer for every row: its `verdict` on
        # the checkout's URLs; its config prefilter (`config_may_guard`) must pass every checkout the gate guards, since
        # it only narrows; and a whole `sweep` over the checkouts must install the hook in exactly the guarded ones, and
        # defer none. So a change to either reader fails here until the other follows.
        self.keep_cache_fresh()
        # Neither GIT_SSH_COMMAND (which setUp sets to block git's ssh transport) nor GIT_SSH: either makes every alias
        # unknown, and the table's alias rows are about ssh -G. The "sshCommand" rows set the checkout's own.
        env = {k: v for k, v in self.env.items() if not k.startswith("GIT_CONFIG") and k not in ("GIT_SSH_COMMAND", "GIT_SSH")} \
            | {"GIT_CONFIG_NOSYSTEM": "1"}
        checkouts = self.guard_checkouts(env)

        def judge(script: str, *args: str) -> list[str]:
            r = subprocess.run(["bash", "-c", script, "judge", *args], env=env, capture_output=True, text=True, check=True)
            return r.stdout.split()

        want = [w for w, *_ in GUARD_TABLE]
        rows = [f"{name} {url} {how}".strip() for _, name, url, how in GUARD_TABLE]
        # The gate's functions, cut out of shims/git; under `bash -c` there is no BASH_SOURCE to find the helper by, so
        # it is named as the shim finds it, beside this shims dir.
        gate = judge(f"eval \"$(awk '/^jj_push_refuse\\(\\)/,/^jj_push_gate\\(\\)/' '{SHIMS / 'git'}' | sed '$d')\"\n"
                     f"jj_gate_visibility='{SHIMS.parent / 'scripts' / 'github-repo-visibility'}'\n"
                     # As shims/git runs it for a push from CO: its git, and the global options naming the repository.
                     'for co in "$@"; do _real=/usr/bin/git args=(-C "$co") i=2\n'
                     '  if jj_push_guarded "$(/usr/bin/git -C "$co" remote -v)"; then echo Y; else echo N; fi; done\n',
                     *checkouts)
        self.assertEqual(list(zip(gate, rows)), list(zip(want, rows)))
        sweeper = self.check.parent / "sweep-hooks.sh"
        if not sweeper.is_file():
            self.skipTest("no sweep-hooks.sh beside the check to compare with")
        text = sweeper.read_text(encoding="utf-8")
        missing = [f for f in ("verdict", "config_may_guard", "config_of", "sweep") if not re.search(rf"(?m)^{f}\(\) \{{", text)]
        self.assertEqual(missing, [], f"{sweeper} has no such functions: it predates the sweep of dispatch://BET-706; install that one")
        here = self.tmp / "sweep-here"
        here.mkdir()
        installed = self.tmp / "installed"
        # Records its checkout and writes a pre-push hook with the wrapper's marker where git runs it, which is what
        # the sweep reads back before it calls a checkout INSTALLED.
        (here / "install-prepush-hook.sh").write_text(
            f'#!/bin/bash\nprintf "%s\\n" "$1" >> "{installed}"\n'
            'hook="$(/usr/bin/git -C "$1" rev-parse --path-format=absolute --git-path hooks/pre-push)" || exit 1\n'
            'mkdir -p "${hook%/*}" && printf "#!/bin/sh\\n# PUBLIC-LEAK-CHECK-WRAPPER\\n" > "$hook" && chmod +x "$hook"\n',
            encoding="utf-8")
        (here / "install-prepush-hook.sh").chmod(0o755)
        load = f"eval \"$(sed '/^i=0$/,$d' '{sweeper}')\"\nhere='{here}'; log='{here}/log'\n"
        own = judge(load + 'for co in "$@"; do\n'
                    # The checkout's URLs as the gate reads them, after the tab (a line with none is no remote's, such
                    # as the rest of a filter holding a newline): a push line's without its final " (push)", a fetch
                    # line's up to its first " (fetch)" (a partial clone's filter follows it).
                    '  mapfile -t urls < <(/usr/bin/git -C "$co" remote -v | sed -E "/\t/!d; s/^[^\t]*\t//; / \\(push\\)$/{s/ \\(push\\)$//;b}; s/ \\(fetch\\).*$//" | sort -u)\n'
                    # -C names the checkout, so the sweep reads its core.sshCommand, as `sweep` does.
                    '  v=$(verdict -C "$co" "${urls[@]}")\n'
                    '  if config_may_guard "$(config_of "$co/.git" "$co")"; then p=P; else p=S; fi\n'
                    '  case $v in guard) echo "Y$p" ;; no) echo "N$p" ;; *) echo "${v:-empty}$p" ;; esac\n'
                    'done\n', *checkouts)
        self.assertEqual(list(zip([o[:-1] for o in own], rows)), list(zip(want, rows)), "the sweep's verdict is not the gate's")
        self.assertEqual([row for g, o, row in zip(gate, own, rows) if g == "Y" and o[-1] != "P"], [],
                         "the sweep's config prefilter skips a checkout the gate guards")
        judge(load + 'sweep "$1"\n', str(self.tmp / "guard"))
        log = (here / "log").read_text(encoding="utf-8")
        self.assertNotIn("DEFERRED", log)
        hooked = set(installed.read_text(encoding="utf-8").split("\n")) - {""} if installed.exists() else set()
        self.assertEqual(sorted(hooked), sorted(co for co, g in zip(checkouts, gate) if g == "Y"), "the sweep hooks other checkouts than the gate guards")
        self.assertRegex(log.splitlines()[-1], r" sweep \[[^\]]*\]: (\d+) checkouts.*, (\d+) installed, (\d+) failed$")

    # Where: the cases where git's pre-push hook would run the check, and those only.
    def test_the_sjawhar_fork_is_checked(self):
        # All 14 jj checkouts of theorem-labs/rocq here push to `sjawhar` too, and the cache calls sjawhar/rocq
        # public; the hook checks a `git push sjawhar` from them, so the gate checks the jj one.
        fork = "https://github.com/sjawhar/rocq.git"
        self.remote("sjawhar", fork)
        c = self.leak()
        self.ok("bookmark", "track", "b1@sjawhar")
        self.assert_refused(self.jj("git", "push", "--remote", "sjawhar", "-b", "b1"), c, fork)

    def test_a_clean_push_to_the_sjawhar_fork_passes(self):
        fork = "https://github.com/sjawhar/rocq.git"
        self.remote("sjawhar", fork)
        c = self.commit("guard.v", "(* clean; BET-525 *)", "Port the guard check", "b1")
        self.ok("bookmark", "track", "b1@sjawhar")
        r = self.jj("git", "push", "--remote", "sjawhar", "-b", "b1")
        self.assert_pushed(r, c, fork)
        self.assertIn("LEAK CHECK: clean", r.stderr)

    def test_a_leak_to_sjawhar_dotfiles_is_refused(self):
        # sjawhar/dotfiles was scrubbed (dispatch://INFRA-974, landed 2026-11-02 05:01Z) and is now a public target like
        # any other: the sweep hooks a checkout of it, and the gate checks a jj push from one. Revisions 1 to 3 left
        # every push to it alone, pending Sami's answer (dispatch://BET-214/ask/fd494519).
        self.assertEqual(CACHE["sjawhar_dotfiles"], "public")
        for n, url in enumerate(("https://github.com/sjawhar/dotfiles.git", "git@github.com:sjawhar/dotfiles.git")):
            self.git("remote", "set-url", "origin", url)  # the checkout's only remote
            c = self.leak(path=f"dots{n}.v")
            self.assert_refused(self.jj("git", "push", "-b", "b1"), c, url)
        self.ok("new", self.init)
        clean = self.commit("clean.v", "(* clean *)", "Clean", "b2")
        r = self.jj("git", "push", "-b", "b2")
        self.assert_pushed(r, clean, url)
        self.assertIn("LEAK CHECK: clean", r.stderr)

    def assert_ref_kept(self, url: str, ref: str, sha: str):
        """The sink of URL holds REF at SHA, as before the refused push."""
        r = subprocess.run(["/usr/bin/git", "--git-dir", str(self.sink_of(url)), "rev-parse", "--verify", "-q", ref], capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), sha, f"{ref} at {url} moved")

    def refused_then_clean(self, url: str, gh: bool = False, at: str | None = None):
        """URL as the checkout's only remote, whose sink (that of AT, by default URL) holds refs/heads/b1 at a clean
        commit: a jj push of a leaking commit on top is refused by the check and leaves that ref where it was; a clean
        one goes out."""
        at = at or url
        self.git("remote", "set-url", "origin", url)
        base = self.commit("base.v", "(* clean *)", "Base", "b1")
        self.assert_pushed(self.jj("git", "push", "-b", "b1"), base, at)
        self.assert_ref_kept(at, "refs/heads/b1", base)
        c = self.leak()
        r = self.jj("git", "push", "-b", "b1")
        self.assert_refused(r, c, url, gh=gh)
        self.assertNotIn(at, self.received(c))
        self.assert_ref_kept(at, "refs/heads/b1", base)
        self.ok("bookmark", "set", "--allow-backwards", "b1", "-r", base)
        self.ok("new", base)
        clean = self.commit("clean.v", "(* clean *)", "Clean", "b1")
        r = self.jj("git", "push", "-b", "b1")
        self.assert_pushed(r, clean, at)
        self.assertIn("LEAK CHECK: clean", r.stderr)
        self.assert_ref_kept(at, "refs/heads/b1", clean)

    def test_a_transport_prefixed_url_is_guarded(self):
        # dispatch://BET-706 A3: git's <transport>::<address> form names the repository at the address, here a public
        # theorem-labs one; the old helper read the transport as an ssh host and said not-github. GUARD_TABLE holds the
        # https:: form; pushed for real, https:: would run git's own git-remote-https, which git finds in its exec
        # path before PATH and which would reach GitHub, so this push names the test's sink helper as the transport.
        address = "https://github.com/theorem-labs/rocq"
        self.refused_then_clean(f"sink::{address}", at=address)

    def test_the_visibility_helper_reads_each_url_form(self):
        # scripts/github-repo-visibility --slug, the parse the gate and shims/jj rely on (the plan's shared contract): a
        # leading <transport>:: and git+ are stripped; an ssh alias resolves through `ssh -G` with the URL's user; a
        # host part that cannot be parsed is unknown; a local path, file:// or another host is not-github (shims/jj
        # skips those, so a local remote must never become unknown there).
        helper = SHIMS.parent / "scripts" / "github-repo-visibility"
        cases = {
            "https::https://github.com/theorem-labs/rocq": "theorem-labs/rocq",
            "sink::https://github.com/Theorem-Labs/Rocq.git": "theorem-labs/rocq",
            "ssh::git@github.com:theorem-labs/rocq.git": "theorem-labs/rocq",
            "git+https://github.com/theorem-labs/rocq": "theorem-labs/rocq",
            "git+ssh://git@github.com/theorem-labs/rocq": "theorem-labs/rocq",
            "ssh+git://github.com/theorem-labs/rocq": "theorem-labs/rocq",
            "ssh://git@ssh.github.com:443/theorem-labs/rocq.git": "theorem-labs/rocq",
            "git@alias-xf:theorem-labs/rocq.git": "theorem-labs/rocq",
            "ssh://git@alias-xf/theorem-labs/rocq.git": "theorem-labs/rocq",
            "git@useralias:theorem-labs/rocq": "theorem-labs/rocq",
            "useralias:theorem-labs/rocq": "not-github",
            "upperalias:theorem-labs/rocq": "not-github",
            "git@-oProxyCommand=x:theorem-labs/rocq": "unknown",
            "ssh:///theorem-labs/rocq.git": "unknown",
            "git@:theorem-labs/rocq.git": "unknown",
            "-oProxyCommand=x@alias-xf:theorem-labs/rocq": "unknown",
            "https://x-access-token:not-a-secret@github.com/theorem-labs/rocq.git": "theorem-labs/rocq",
            "ssh://git@[github.com]:22/theorem-labs/rocq": "theorem-labs/rocq",
            "git@[github.com]:theorem-labs/rocq": "theorem-labs/rocq",
            "[git@github.com]:theorem-labs/rocq": "theorem-labs/rocq",
            "ssh://git@[alias-xf]:22/theorem-labs/rocq": "theorem-labs/rocq",
            "https://bad host/theorem-labs/rocq": "unknown",
            "https://?/theorem-labs/rocq": "unknown",
            "https://[github.com/theorem-labs/rocq": "unknown",
            "https://[::1]/theorem-labs/rocq": "not-github",
            "ssh://git@portalias:2222/theorem-labs/rocq": "theorem-labs/rocq",
            "ssh://git@portalias/theorem-labs/rocq": "not-github",
            "git@portalias:theorem-labs/rocq": "not-github",
            "https://github.com:abc/theorem-labs/rocq": "unknown",
            "https://a@b@github.com/theorem-labs/rocq": "unknown",
            "https://[bad host]/theorem-labs/rocq": "unknown",
            "https://www.github.com/theorem-labs/rocq": "theorem-labs/rocq",
            "git@WWW.GitHub.com:Theorem-Labs/Rocq.git": "theorem-labs/rocq",
            "https://github.com": "unknown",
            "https://github.com/theorem-labs/rocq space.git": "unknown",
            "https://notgithub.com/theorem-labs/rocq.git": "not-github",
            "https://example.com": "not-github",
            "git@corp.example:theorem-labs/rocq.git": "not-github",
            "file:///srv/theorem-labs/rocq.git": "not-github",
            "/srv/theorem-labs/rocq.git": "not-github",
            "../a.git": "not-github",
            "./a.git": "not-github",
            "origin": "not-github",
            "sink::/srv/theorem-labs/rocq.git": "not-github",
            "https://github.com./theorem-labs/rocq": "theorem-labs/rocq",
            "git@WWW.GITHUB.COM.:theorem-labs/rocq": "theorem-labs/rocq",
            "git@dotalias:theorem-labs/rocq": "theorem-labs/rocq",
            "https://github.com../theorem-labs/rocq": "not-github",
        }
        env = {k: v for k, v in self.env.items() if k not in ("GIT_SSH_COMMAND", "GIT_SSH")}
        got = {url: subprocess.run([str(helper), "--slug", url], env=env, capture_output=True, text=True).stdout.strip() for url in cases}
        self.assertEqual(got, cases)
        # An ssh command of git's own (GIT_SSH_COMMAND, GIT_SSH) may resolve an alias otherwise than ssh -G: unknown.
        # A literal GitHub host, a non-ssh URL and a local path are decided as before.
        for var in ("GIT_SSH_COMMAND", "GIT_SSH"):
            for url, want in (("git@alias-xf:theorem-labs/rocq.git", "unknown"), ("git@corp.example:theorem-labs/rocq.git", "unknown"),
                              ("git@github.com:theorem-labs/rocq.git", "theorem-labs/rocq"), ("https://notgithub.com/o/r", "not-github"),
                              ("../a.git", "not-github")):
                r = subprocess.run([str(helper), "--slug", url], env=env | {var: "ssh -F /nonexistent"}, capture_output=True, text=True)
                self.assertEqual((var, url, r.stdout.strip()), (var, url, want))

    def test_a_public_fork_outside_any_list_is_guarded(self):
        # dispatch://BET-706 R1/A3: sjawhar/oh-my-pi was on no list and has no cache entry (the check writes entries
        # only for theorem-labs); gh says it is public, so the checkout is guarded.
        (self.ghdir / "gh").write_text(
            f'#!/bin/bash\nprintf \'%s\\n\' "$*" >> "{self.ghlog}"\n'
            '[[ " $* " == *" repos/sjawhar/oh-my-pi "* ]] && { echo public; exit 0; }\nexit 1\n', encoding="utf-8")
        self.refused_then_clean("https://github.com/sjawhar/oh-my-pi.git", gh=True)
        self.assertIn("api repos/sjawhar/oh-my-pi --jq .visibility", self.gh_calls())

    def test_a_public_fork_with_no_answer_from_gh_is_guarded(self):
        # dispatch://BET-706 R4: when the visibility cannot be read, the repository counts as public.
        self.refused_then_clean("https://github.com/sjawhar/oh-my-pi.git", gh=True)
        self.assertIn("api repos/sjawhar/oh-my-pi --jq .visibility", self.gh_calls())

    def test_every_other_target_of_a_guarded_checkout_is_decided_by_the_check(self):
        # As for a git push from this checkout: a private target passes, a public or non-GitHub one is scanned, one
        # whose visibility cannot be read is refused.
        # The check reads the visibility of every remote of the checkout, so the unreadable one is added last.
        targets = {"private": PRIVATE, "upstream": "https://github.com/rocq-prover/rocq.git",
                   "fake": "https://notgithub.com/sjawhar/rocq.git", "local": str(self.tmp / "build-box.git")}
        for remote, url in targets.items():
            self.remote(remote, url)
        c = self.leak()
        for remote in targets:
            self.ok("bookmark", "track", f"b1@{remote}")
        r = self.jj("git", "push", "--remote", "private", "-b", "b1")
        self.assert_pushed(r, c, PRIVATE)
        for remote in ("upstream", "fake", "local"):
            self.assert_refused(self.jj("git", "push", "--remote", remote, "-b", "b1"), c, targets[remote])
        unknown = "https://github.com/theorem-labs/never-looked-up.git"
        self.remote("unknown", unknown)
        self.ok("bookmark", "track", "b1@unknown")
        r = self.jj("git", "push", "--remote", "unknown", "-b", "b1")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("error: LEAK CHECK: cannot read the visibility of theorem-labs/never-looked-up", r.stderr)
        self.assertEqual(self.received(c), [PRIVATE])
        self.assertTrue(self.gh_calls(), "the visibility read went to the fake gh")

    def test_a_fully_qualified_github_ssh_host_is_guarded(self):
        # The red team's finding 1, over git's own ssh transport (the local-only adapter), the checkout's only remote:
        # ssh.github.com. is ssh.github.com. A clean commit goes out; a leaking one on top is refused and the far
        # side's ref stays at the clean one.
        self.use_ssh()
        url = "ssh://git@ssh.github.com.:443/theorem-labs/rocq.git"
        self.git("remote", "set-url", "origin", url)
        self.git("config", "--unset-all", "remote.origin.vcs")
        sink = self.ssh_sink("git@ssh.github.com.", "/theorem-labs/rocq.git")
        base = self.commit("base.v", "(* clean *)", "Base", "b1")
        r = self.jj("git", "push", "-b", "b1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("LEAK CHECK: clean", r.stderr)
        ref = lambda: subprocess.run(["/usr/bin/git", "--git-dir", str(sink), "rev-parse", "-q", "--verify", "refs/heads/b1"],
                                     capture_output=True, text=True).stdout.strip()
        self.assertEqual(ref(), base)
        c = self.leak()
        r = self.jj("git", "push", "-b", "b1")
        self.assertNotEqual(r.returncode, 0, r.stderr)
        self.assertIn("LEAK CHECK: refusing to push", r.stderr)
        self.assertFalse(self.holds(sink, c), f"{c} reached {url}")
        self.assertEqual(ref(), base)

    def test_a_helper_that_fails_counts_as_unknown(self):
        # The deep review's finding 3 (spec R4): a helper exit other than 0 is unknown whatever it printed, so the
        # checkout is guarded. The gate's own jj_push_guarded, with a stand-in helper.
        helper = self.tmp / "failing-helper"
        listing = "origin\thttps://github.com/theorem-labs/private-fixture.git (fetch)\n"
        script = (f"eval \"$(awk '/^jj_push_refuse\\(\\)/,/^jj_push_gate\\(\\)/' '{SHIMS / 'git'}' | sed '$d')\"\n"
                  f"jj_gate_visibility='{helper}' _real=/usr/bin/git args=(-C '{self.repo}') i=2\n"
                  'if jj_push_guarded "$1"; then echo Y; else echo N; fi\n')
        for body, want in (("echo private\nexit 7", "Y"), ("echo not-github\nexit 1", "Y"), ("exit 3", "Y"), ("echo private", "N")):
            helper.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
            helper.chmod(0o755)
            r = subprocess.run(["bash", "-c", script, "judge", listing], env=self.env, capture_output=True, text=True, check=True)
            self.assertEqual((body, r.stdout.strip()), (body, want))

    # v8 regression, reported to ROCQ 2026-11-05 22:3xZ: leak-check-v8 calls this leak clean. Expected to fail
    # against v8 only (v8_regression); unchanged, and run in full, against every other check.
    @v8_regression
    def test_a_checkouts_own_ssh_command_makes_an_alias_unknown(self):
        # The checkout's only remote is an alias ssh -G resolves to corp.example, which is not GitHub: unguarded, the
        # push goes out as jj asked. With core.sshCommand set in the checkout, git would run that command, which may
        # resolve the alias otherwise, so the alias is unknown and the checkout guarded: the check refuses the leak.
        self.env.pop("GIT_SSH_COMMAND")  # through git-remote-sink: no ssh runs
        url = "git@corp.example:theorem-labs/rocq.git"
        self.git("remote", "set-url", "origin", url)
        c = self.leak()
        r = self.jj("git", "push", "-b", "b1")
        self.assert_pushed(r, c, url)
        self.assertNotIn("LEAK CHECK", r.stderr)
        self.git("config", "core.sshCommand", "ssh -F /nonexistent")
        c = self.leak(path="guard2.v")
        # Spec R4: a target whose visibility cannot be read is refused before anything is scanned, so the check may
        # refuse this target unread (as assert_refused_unreadable expects) or refuse the leaked content. Either way it
        # must refuse, and nothing may reach the receiver.
        r = self.jj("git", "push", "-b", "b1")
        if "LEAK CHECK: cannot read the visibility of " in r.stderr:
            self.assertNotEqual(r.returncode, 0, r.stderr)
            self.assertIn(f"error: git shim: the public leak check refused this jj push to {url}", r.stderr)
            self.assertNotIn(url, self.received(c))
        else:
            self.assert_refused(r, c, url)

    def test_an_unguarded_checkout_is_untouched(self):
        # Every remote is private or not GitHub, even where its path names theorem-labs/rocq: the sweep installs no
        # hook, nor does the gate check, whatever the targets and the content. git runs exactly as jj asked:
        # --no-verify, no hooksPath. Every remote goes through git-remote-sink, so setUp's GIT_SSH_COMMAND, which would
        # make the corp.example alias unknown, is not needed and is left out.
        self.env.pop("GIT_SSH_COMMAND")
        self.git("remote", "set-url", "origin", PRIVATE)
        targets = {"origin": PRIVATE, "corp": "git@corp.example:theorem-labs/rocq.git",
                   "local": str(self.tmp / "elsewhere" / "rocq.git"), "fake": "https://notgithub.com/sjawhar/rocq.git"}
        for remote, url in targets.items():
            if remote != "origin":
                self.remote(remote, url)
        c = self.leak()
        for remote, url in targets.items():
            self.ok("bookmark", "track", f"b1@{remote}")
            r = self.jj("git", "push", "--remote", remote, "-b", "b1")
            self.assert_pushed(r, c, url)
            self.assertNotIn("LEAK CHECK", r.stderr)
            self.assertNotIn("git shim", r.stderr)
        pushes = [ln for ln in self.calls.read_text(encoding="utf-8").splitlines() if " push " in f" {ln} "]
        self.assertEqual(len(pushes), 4)
        self.assertTrue(all("--no-verify" in p and "hooksPath" not in p for p in pushes), pushes)
        self.assertEqual(self.gh_calls(), [])

    def test_a_private_partial_clone_is_untouched(self):
        # The round-5 reviews: git prints a partial clone's filter after its fetch line (`<url> (fetch) [blob:none]`).
        # Read as part of the URL it made a private repository unknown, so the gate guarded the checkout and the check,
        # which scans a push to any owner but theorem-labs as public, refused a leaking push to the private
        # sjawhar/private-fixture. Every remote here is private and a partial clone's: git runs as jj asked. Then a
        # public partial-clone remote guards the checkout, and the check refuses the same content to it.
        self.env.pop("GIT_SSH_COMMAND")  # through git-remote-sink: no ssh runs
        external = "https://github.com/sjawhar/private-fixture.git"
        self.git("remote", "set-url", "origin", PRIVATE)
        self.remote("ext", external)
        # One filter of each kind: blob:none, and one whose own text holds brackets.
        self.make_partial(self.repo, "origin", self.env)
        self.make_partial(self.repo, "ext", self.env, "sparse:oid=HEAD:sparse [x]")
        c = self.leak()
        for remote, url in (("origin", PRIVATE), ("ext", external)):
            self.ok("bookmark", "track", f"b1@{remote}")
            r = self.jj("git", "push", "--remote", remote, "-b", "b1")
            self.assert_pushed(r, c, url)
            self.assertNotIn("LEAK CHECK", r.stderr)
            self.assertNotIn("git shim", r.stderr)
        self.remote("pub", ROCQ)
        self.make_partial(self.repo, "pub", self.env)
        self.ok("bookmark", "track", "b1@pub")
        self.assert_refused(self.jj("git", "push", "--remote", "pub", "-b", "b1"), c, ROCQ)

    # v8 regression, reported to ROCQ 2026-11-05 22:3xZ: leak-check-v8 calls the leak clean while the cache says
    # `public`. Expected to fail against v8 only (v8_regression); unchanged, and run in full, against every other check.
    @v8_regression
    def test_the_cache_decides_and_an_unreadable_one_refuses(self):
        # dispatch://BET-706 R4: the gate reads the check's cache entry for the repository, lower-cased and only while
        # fresh (600 s for public, 60 s for private or internal), then gh. `private` or `internal` leaves the checkout
        # alone; `public` guards it, so the check refuses leaking content. Where neither answers (no entry; an entry
        # that is empty, unreadable or holds a word the check never writes; a mixed-case file name, which the
        # lower-cased read does not find; a cache directory that cannot be read: the reviewer's B1, cases 1 and 2) the
        # visibility is unknown, so the checkout is guarded, and the check, which cannot read the visibility either,
        # refuses the push without scanning it. Before BET-706 the gate passed a checkout with no entry and refused the
        # others unread itself.
        url = "https://github.com/theorem-labs/newly-public.git"
        self.git("remote", "set-url", "origin", url)
        entry = self.cache / "theorem-labs_newly-public"
        n = 0

        def attempt(quiet: bool = False) -> tuple[str, subprocess.CompletedProcess[str]]:
            nonlocal n
            n += 1
            c = self.commit(f"g{n}.v", self.leak_text, f"Leak {n}", "b1")
            return c, self.jj(*(["--quiet"] if quiet else []), "git", "push", "-b", "b1")

        def refused_unreadable(quiet: bool = False):
            c, r = attempt(quiet)
            self.assert_refused_unreadable(r, c, url, "theorem-labs/newly-public")

        refused_unreadable()  # no entry, and the fake gh gives no answer
        self.assertIn("api repos/theorem-labs/newly-public --jq .visibility", self.gh_calls())
        for word in ("private", "internal"):
            entry.write_text(word, encoding="utf-8")
            c, r = attempt()
            self.assert_pushed(r, c, url)
            self.assertNotIn("LEAK CHECK", r.stderr)
        (self.cache / "theorem-labs_other").write_text("", encoding="utf-8")
        c, r = attempt()
        self.assert_pushed(r, c, url)  # the empty entry is another repository's
        for word in ("public", "public\n"):
            entry.write_text(word, encoding="utf-8")
            self.ghlog.unlink(missing_ok=True)
            c, r = attempt()
            self.assert_refused(r, c, url)
        entry.unlink()
        (self.cache / "Theorem-Labs_Newly-Public").write_text("public", encoding="utf-8")
        refused_unreadable()
        (self.cache / "Theorem-Labs_Newly-Public").unlink()
        for word in ("", "Unknown"):
            entry.write_text(word, encoding="utf-8")
            refused_unreadable(quiet=True)
        entry.write_text("public", encoding="utf-8")
        entry.chmod(0)
        refused_unreadable(quiet=True)
        entry.chmod(0o644)
        self.cache.chmod(0)
        try:
            refused_unreadable(quiet=True)
        finally:
            self.cache.chmod(0o755)

    def test_a_cache_entry_rewritten_during_the_push_still_guards(self):
        # The reviewer's B1 case 3: the entry goes empty while the gate lists the remotes, as an entry the check is
        # rewriting in place does, and is written again before git pushes. The gate reads the empty entry as no answer,
        # asks gh, which gives none, so the checkout is guarded (dispatch://BET-706 R4), and the check, reading the
        # rewritten entry, refuses the leaking content.
        url = "https://github.com/theorem-labs/cached-public.git"
        self.git("remote", "set-url", "origin", url)
        entry = self.cache / "theorem-labs_cached-public"
        racer = self.tmp / "racer"
        racer.mkdir()
        (racer / "git").write_text(
            "#!/bin/bash\n"
            f'if [[ " $* " == *" remote -v "* ]]; then : > "{entry}"; fi\n'
            f'for a in "$@"; do [[ "$a" == push ]] && {{ printf public > "{entry}"; break; }}; done\n'
            f'exec "{self.fake / "git"}" "$@"\n', encoding="utf-8")
        (racer / "git").chmod(0o755)
        self.env["PATH"] = self.path_with(f"{SHIMS}{os.pathsep}{racer}")
        c = self.leak()
        self.assert_refused(self.jj("git", "push", "-b", "b1"), c, url, gh=True)
        self.assertEqual(self.gh_calls(), ["api repos/theorem-labs/cached-public --jq .visibility"], "the race did not run as staged")

    def test_an_unsearchable_parent_of_the_cache_refuses(self):
        # The revision-2 review's R2-1: the cache and its public entry exist, but the directory above them cannot be
        # searched. The gate cannot read the entry, asks gh, which gives no answer, so the checkout is guarded
        # (dispatch://BET-706 R4), and the check, which cannot read the visibility either, refuses as git's installed
        # hook does for the same push. Before BET-706 the gate refused a repository outside its fixed list unread.
        self.install_git_hook()
        parent = self.cache.parent
        self.git("remote", "set-url", "origin", "git@github.com:theorem-labs/rocq.git")
        c = self.leak()
        parent.chmod(0)
        try:
            g = self.git_push("origin", c, "git-case")
            r = self.jj("--quiet", "git", "push", "-b", "b1")
        finally:
            parent.chmod(0o755)
        self.assertNotEqual(g.returncode, 0, g.stderr)
        self.assertIn("LEAK CHECK: cannot read the visibility of theorem-labs/rocq", g.stderr)
        self.assertNotEqual(r.returncode, 0, r.stderr)
        self.assertIn("error: LEAK CHECK: cannot read the visibility of theorem-labs/rocq", r.stderr)
        self.assertIn("error: git shim: the public leak check refused this jj push to git@github.com:theorem-labs/rocq.git", r.stderr)
        self.assertEqual(self.received(c), [])
        url = "https://github.com/theorem-labs/cached-public.git"
        self.git("remote", "set-url", "origin", url)
        c = self.leak(path="guard2.v")
        parent.chmod(0)
        try:
            r = self.jj("--quiet", "git", "push", "-b", "b1")
        finally:
            parent.chmod(0o755)
        self.assert_refused_unreadable(r, c, url, "theorem-labs/cached-public")
        # A cache that is not there at all, or a symlink to nothing, is no answer either: the same.
        self.cache.rename(self.tmp / "cache-away")
        try:
            r = self.jj("--quiet", "git", "push", "-b", "b1")
            self.cache.symlink_to(self.tmp / "nowhere")
            r2 = self.jj("--quiet", "git", "push", "-b", "b1")
        finally:
            self.cache.unlink(missing_ok=True)
            (self.tmp / "cache-away").rename(self.cache)
        self.assert_refused_unreadable(r, c, url, "theorem-labs/cached-public")
        self.assert_refused_unreadable(r2, c, url, "theorem-labs/cached-public")

    def hidden_push(self, path: Path, remote: str = "origin", commit: str = "") -> tuple[subprocess.CompletedProcess[str], subprocess.CompletedProcess[str]]:
        """As the revision-3 review staged R3-1: PATH moved into $TMP/hidden, a symlink to it left in its place, and
        search permission taken from hidden; then a plain git push of COMMIT through the installed hook, and the jj push
        of b1. Everything is put back before returning (git's result, jj's result)."""
        hidden = self.tmp / "hidden"
        hidden.mkdir()
        moved = hidden / path.name
        path.rename(moved)
        path.symlink_to(moved)
        hidden.chmod(0)
        try:
            return self.git_push(remote, commit, "git-case"), self.jj("--quiet", "git", "push", "-b", "b1")
        finally:
            hidden.chmod(0o755)
            path.unlink()
            moved.rename(path)
            hidden.rmdir()

    def test_a_cache_behind_a_symlink_into_an_unsearchable_directory_refuses(self):
        # The revision-3 review's R3-1: `.cache`, `public-leak-check` or the deciding entry is a symlink into a directory
        # that cannot be searched. The entry exists and says public, but cannot be read, so jj once sent the leak while
        # git's installed hook refused it. Now the gate reads no answer, asks gh, which gives none, and guards the
        # checkout (dispatch://BET-706 R4); the check, which cannot read the visibility either, refuses the jj push as
        # git's hook refuses the git one.
        self.install_git_hook()
        url = "https://github.com/theorem-labs/cached-public.git"
        self.git("remote", "set-url", "origin", url)
        c = self.leak()
        entry = self.cache / "theorem-labs_cached-public"
        for path in (self.home / ".cache", self.cache, entry):
            g, r = self.hidden_push(path, commit=c)
            self.assertNotEqual(g.returncode, 0, g.stderr)
            self.assertIn("LEAK CHECK: cannot read the visibility of theorem-labs/cached-public", g.stderr)
            self.assert_refused_unreadable(r, c, url, "theorem-labs/cached-public")
        # The review's control over git's own ssh transport: the `.cache` variant with only an ssh alias of github.com
        # as the remote. The gate reads no visibility and guards; the check, which does not resolve ssh aliases, scans
        # the target as one outside GitHub. Both pushes are refused for the content, and the commit never reaches the
        # far side.
        self.use_ssh()
        url = "git@alias-xf:theorem-labs/cached-public.git"
        self.git("remote", "set-url", "origin", url)
        self.git("config", "--unset-all", "remote.origin.vcs")
        sink = self.ssh_sink("git@alias-xf", "theorem-labs/cached-public.git")
        g, r = self.hidden_push(self.home / ".cache", commit=c)
        self.assertNotEqual(g.returncode, 0, g.stderr)
        self.assertIn("LEAK CHECK: refusing to push", g.stderr)
        self.assert_refused(r, c, url, gh=True)
        self.assertFalse(self.holds(sink, c), f"{c} reached {url}")
        self.assertTrue(sink.is_dir(), "git did not reach the ssh adapter")

    # What git's hook would pass, the gate passes.
    def test_an_up_to_date_push_is_not_refused(self):
        # The reviewer's F1: the target already holds the leaking commit and this clone has no remote-tracking ref for
        # it. jj thinks the bookmark is new; git sends nothing, so it gives the hook nothing to scan.
        c = self.leak()
        subprocess.run(["/usr/bin/git", "push", "-q", "--no-verify", "origin", f"{c}:refs/heads/b1"], cwd=self.repo, env=self.env,
                       check=True, capture_output=True)
        self.git("update-ref", "-d", "refs/remotes/origin/b1")
        self.assertNotIn("b1@origin", self.ok("bookmark", "list", "--all-remotes"))
        r = self.jj("git", "push", "-b", "b1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("refusing", r.stderr)
        self.assertNotIn("error:", r.stderr)

    def test_the_checked_destination_is_the_pushed_destination(self):
        # The reviewer's B3, first case: the check passes the private origin, then origin is pointed at the public
        # repository. git had already connected to the URL it gave the hook, so the commit goes there and nowhere else.
        self.remote("public", ROCQ)
        self.git("remote", "set-url", "origin", PRIVATE)
        mover = self.tmp / "check-then-retarget"
        mover.write_text(f'#!/bin/bash\nbash "{self.check}" "$@" || exit $?\n/usr/bin/git remote set-url origin {ROCQ}\n', encoding="utf-8")
        mover.chmod(0o755)
        self.env["PUBLIC_LEAK_CHECK"] = str(mover)
        c = self.leak()
        r = self.jj("git", "push", "--remote", "origin", "-b", "b1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.git("remote", "get-url", "origin"), ROCQ, "the retarget did not run")
        self.assertEqual(self.received(c), [PRIVATE])

    def test_the_same_verdict_as_the_git_hook(self):
        # For each target, the jj push and a plain `git push` of the same commit through the hook
        # install-prepush-hook.sh installs, both sent for real to the sinks: refused alike, sent alike, sjawhar/dotfiles
        # included since its scrub (dispatch://INFRA-974).
        self.install_git_hook()
        targets = {
            "origin": ROCQ,
            "sjawhar": "https://github.com/sjawhar/rocq.git",
            "upstream": "https://github.com/rocq-prover/rocq.git",
            "local": str(self.tmp / "build-box.git"),
            "alias": "git@alias-xf:theorem-labs/rocq.git",
            "private": PRIVATE,
            "dots": "https://github.com/sjawhar/dotfiles.git",
        }
        for remote, url in targets.items():
            if remote != "origin":
                self.remote(remote, url)
        c = self.leak()
        verdicts = {}
        for remote, url in targets.items():
            self.ok("bookmark", "set", f"j-{remote}", "-r", c)
            if remote != "origin":
                self.ok("bookmark", "track", f"j-{remote}@{remote}")
            g = self.git_push(remote, c, f"g-{remote}")
            j = self.jj("git", "push", "--remote", remote, "-b", f"j-{remote}")
            verdicts[remote] = ("refused" if g.returncode else "sent", "refused" if j.returncode else "sent")
        self.assertEqual(verdicts, {
            "origin": ("refused", "refused"),
            "sjawhar": ("refused", "refused"),
            "upstream": ("refused", "refused"),
            "local": ("refused", "refused"),
            "alias": ("refused", "refused"),
            "private": ("sent", "sent"),
            "dots": ("refused", "refused"),
        })
        self.assertEqual(self.received(c), [PRIVATE])

    def assert_same_refusal_over_git_transport(self, url: str, sink: Path):
        """URL as the checkout's only remote, over git's own transport into SINK: a plain git push through the
        installed hook and the jj push both refuse a fresh leaking commit, and SINK never holds it."""
        self.git("remote", "set-url", "origin", url)
        subprocess.run(["/usr/bin/git", "config", "--unset-all", "remote.origin.vcs"], cwd=self.repo, env=self.env, capture_output=True)
        c = self.leak(path=f"guard-{hashlib.sha1(url.encode()).hexdigest()[:8]}.v")
        g = self.git_push("origin", c, "git-case")
        self.assertNotEqual(g.returncode, 0, g.stderr)
        self.assertIn("LEAK CHECK: refusing to push", g.stderr)
        self.assertFalse(self.holds(sink, c), f"git sent {c} to {url}")
        self.assert_refused(self.jj("git", "push", "-b", "b1"), c, url)
        self.assertFalse(self.holds(sink, c), f"jj sent {c} to {url}")

    def test_a_remote_path_with_a_space_is_read_whole(self):
        # The revision-2 review's R2-2: `git remote -v` keeps a space inside a URL. Read up to the space, this URL is the
        # private theorem-labs/private-fixture, which the gate leaves alone; read whole, its path is no repository
        # name, so its visibility is unknown and the checkout is guarded (dispatch://BET-706 R4). The checkout's only
        # remote, over git's ssh transport (the local-only adapter).
        self.install_git_hook()
        self.use_ssh()
        path = "theorem-labs/private-fixture space.git"
        self.assert_same_refusal_over_git_transport(f"git@alias-xf:{path}", self.ssh_sink("git@alias-xf", path))
        self.assertTrue(self.ssh_sink("git@alias-xf", path).is_dir(), "git did not reach the ssh adapter")

    def test_an_ssh_alias_with_a_hash_or_question_mark_is_guarded(self):
        # The revision-2 review's R2-5: # and ? are ordinary characters of an ssh host alias, and the gate once cut the
        # alias there before it found the path. SSH_CONFIG resolves both aliases to github.com; git hands the whole
        # alias to ssh, here the local-only adapter.
        self.install_git_hook()
        self.use_ssh()
        for alias in ("alias#xf", "alias?xf"):
            resolved = subprocess.run([str(self.ghdir / "ssh"), "-G", f"git@{alias}"], capture_output=True, text=True)
            self.assertIn("hostname github.com", resolved.stdout.splitlines(), resolved.stderr)
            self.assert_same_refusal_over_git_transport(f"git@{alias}:theorem-labs/rocq.git", self.ssh_sink(f"git@{alias}", "theorem-labs/rocq.git"))
            self.assertTrue(self.ssh_sink(f"git@{alias}", "theorem-labs/rocq.git").is_dir(), "git did not reach the ssh adapter")

    def test_the_checkouts_other_hooks_still_run(self):
        # jj's --no-verify stops only pre-push; the repository's other hooks, reference-transaction among them, run
        # for its push. jj-push-hooks/ links every other hook name to its ./hook, which runs them.
        marker = self.tmp / "reftx.log"
        hook = self.repo / ".git" / "hooks" / "reference-transaction"
        hook.write_text(f'#!/bin/bash\necho "$1" >> "{marker}"\n', encoding="utf-8")
        hook.chmod(0o755)
        c = self.commit("guard.v", "(* clean *)", "Clean", "b1")
        self.assert_pushed(self.jj("git", "push", "-b", "b1"), c)
        self.assertIn("committed", marker.read_text(encoding="utf-8").split())

    def test_every_other_hook_is_the_one_git_would_have_run(self):
        # The revision-2 review's R2-3: the gate's last -c core.hooksPath changed which reference-transaction hook git
        # ran when the caller had chosen one on the command line, and the hook it ran saw the gate's -c. For each way
        # of choosing, the jj push through shims/git must run the same hook, with the same GIT_CONFIG_PARAMETERS, as
        # the same jj push through shims/git with the gate cut out, while the gate's pre-push still runs.
        marker = self.tmp / "reftx.log"
        for d in ("own", "selected", "counted"):
            hook = self.tmp / d / "reference-transaction"
            hook.parent.mkdir()
            hook.write_text(f'#!/bin/bash\ncat > /dev/null\nprintf "%s %s %s\\n" {d} "$1" "${{GIT_CONFIG_PARAMETERS-}}" >> "{marker}"\n',
                            encoding="utf-8")
            hook.chmod(0o755)
        self.git("config", "core.hooksPath", str(self.tmp / "own"))
        base = self.tmp / "no-gate-shims"
        shutil.copytree(SHIMS, base, symlinks=True, ignore=shutil.ignore_patterns("tests", "__pycache__"))
        text = (SHIMS / "git").read_text(encoding="utf-8")
        start = text.index("# jj publishes through git")
        tail = '    exec "$_real" "$@"\nfi\n'
        (base / "git").write_text(text[:start] + text[text.index(tail, start) + len(tail):], encoding="utf-8")
        selected = self.tmp / "selected"
        ways = {  # way: (environment, -c put before `push` on jj's git command line, the hook git runs)
            "repository config": ({}, None, "own"),
            "GIT_CONFIG_COUNT": ({"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.hooksPath", "GIT_CONFIG_VALUE_0": str(self.tmp / "counted")},
                                 None, "counted"),
            "GIT_CONFIG_PARAMETERS": ({"GIT_CONFIG_PARAMETERS": f"'core.hooksPath'='{selected}'"}, None, "selected"),
            "an earlier -c": ({}, f"core.hooksPath={selected}", "selected"),
        }
        env, n = self.env, 0
        for way, (extra, injected, want) in ways.items():
            seen = {}
            for arm, shims in (("gate", SHIMS), ("no gate", base)):
                n += 1
                self.env = env
                c = self.commit(f"h{n}.v", "(* clean *)", f"Clean {n}", f"b{n}")
                self.env = {**env, **extra, "PATH": self.path_with(shims, env["PATH"])}
                argv = ["git", "push", "-b", f"b{n}"]
                if injected:
                    w = self.git_wrapper(f"inject-{n}", f'args=(); for a in "$@"; do [[ "$a" == push ]] && args+=(-c "{injected}"); args+=("$a"); done',
                                         shims / "git")
                    argv = ["--config", f"git.executable-path={w}", *argv]
                marker.unlink(missing_ok=True)
                r = self.jj(*argv)
                self.assert_pushed(r, c)
                (self.assertIn if arm == "gate" else self.assertNotIn)("LEAK CHECK: clean", r.stderr, way)
                seen[arm] = marker.read_text(encoding="utf-8").splitlines()
            self.assertEqual(seen["gate"], seen["no gate"], way)
            self.assertEqual([ln.split(" ", 2)[:2] for ln in seen["gate"]], [[want, "prepared"], [want, "committed"]], way)
            self.assertNotIn("jj-push-hooks", "\n".join(seen["gate"]), way)
        self.env = env

    def test_a_hooks_path_from_config_or_environment_does_not_win(self):
        # Each way a caller can choose a hooks directory, pointed at one without the gate's pre-push: it still runs.
        c = self.leak()
        none = str(self.tmp / "none")
        self.env.update({"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.hooksPath", "GIT_CONFIG_VALUE_0": none})
        self.assert_refused(self.jj("git", "push", "-b", "b1"), c)
        del self.env["GIT_CONFIG_COUNT"], self.env["GIT_CONFIG_KEY_0"], self.env["GIT_CONFIG_VALUE_0"]
        self.env["GIT_CONFIG_PARAMETERS"] = f"'core.hooksPath'='{none}'"
        self.assert_refused(self.jj("git", "push", "-b", "b1"), c)
        del self.env["GIT_CONFIG_PARAMETERS"]
        w = self.git_wrapper("inject", f'args=(); for a in "$@"; do [[ "$a" == push ]] && args+=(-c "core.hooksPath={none}"); args+=("$a"); done')
        self.assert_refused(self.jj("--config", f"git.executable-path={w}", "git", "push", "-b", "b1"), c)
        self.git("config", "core.hooksPath", none)
        self.assert_refused(self.jj("git", "push", "-b", "b1"), c)

    def test_another_destinations_failure_is_still_reported(self):
        # The revision-2 review's R2-4: origin pushes to the public rocq, which the gate refuses, and then to a private
        # repository whose server refuses every push in pre-receive. git writes "failed to push some refs" for each;
        # only the one that belongs to the gate's refusal is left out, so the private destination's failure still
        # reaches jj, which reports it from git's porcelain (and then shows none of git's stderr, the gate's lines
        # included). Where the gate refused nothing, git's line stays as git wrote it.
        self.git("config", "--add", "remote.origin.pushurl", ROCQ)
        self.git("config", "--add", "remote.origin.pushurl", PRIVATE)
        declining = self.sink_of(PRIVATE)
        subprocess.run(["/usr/bin/git", "init", "-q", "--bare", str(declining)], check=True)
        (declining / "hooks" / "pre-receive").write_text("#!/bin/bash\ncat > /dev/null\nexit 1\n", encoding="utf-8")
        (declining / "hooks" / "pre-receive").chmod(0o755)
        seen = self.tmp / "git-stderr.log"
        w = self.git_wrapper("tee-stderr", f'exec 2> >(tee -a "{seen}" >&2); args=("$@")')
        failed = "error: failed to push some refs to "
        c = self.leak()
        r = self.jj("--quiet", "--config", f"git.executable-path={w}", "git", "push", "-b", "b1")
        self.assertNotEqual(r.returncode, 0, r.stderr)
        self.assertIn("refs/heads/b1 (reason: pre-receive hook declined)", r.stderr)
        self.assertEqual(self.received(c), [])
        lines = seen.read_text(encoding="utf-8").splitlines()
        self.assertTrue([ln for ln in lines if ln.startswith(f"error: git shim: the public leak check refused this jj push to {ROCQ}")], lines)
        self.assertEqual([ln for ln in lines if ln.startswith(failed)], [f"{failed}'{PRIVATE}'"])
        seen.unlink()
        # The same two destinations for a clean commit: rocq takes it, the private one declines. (jj 0.46 itself then
        # reports the bookmark as pushed and exits 0, from rocq's line of git's porcelain; the gate plays no part.)
        self.ok("new", self.init)  # not on top of the leaking commit, which the check would scan as well
        clean = self.commit("guard2.v", "(* clean *)", "Clean", "b2")
        r = self.jj("--quiet", "--config", f"git.executable-path={w}", "git", "push", "-b", "b2")
        self.assertEqual(self.received(clean), [ROCQ], r.stderr)
        lines = seen.read_text(encoding="utf-8").splitlines()
        self.assertIn("LEAK CHECK: clean", "\n".join(lines))
        self.assertEqual([ln for ln in lines if ln.startswith(failed)], [f"{failed}'{PRIVATE}'"])

    # What passes.
    def test_clean_push_runs_the_check_and_pushes(self):
        c = self.commit("guard.v", "(* clean; BET-525, /mnt/nvme/sami/frac-verify, dispatch://BET-64 *)", "Port the guard check (BET-525)", "b1")
        r = self.jj("git", "push", "-b", "b1")
        self.assert_pushed(r, c)
        self.assertIn("LEAK CHECK: clean", r.stderr)
        self.assertNotIn("error:", r.stderr)

    def test_options_the_old_parser_refused_now_pass(self):
        c = self.commit("guard.v", "(* clean *)", "Clean", "b1")
        self.assert_pushed(self.jj(f"-R={self.repo}", "git", "push", "-b", "b1", cwd=self.tmp), c)
        c = self.commit("guard2.v", "(* clean *)", "Clean", "b1")
        self.assert_pushed(self.jj("--config", "ui.quiet=true", "git", "push", "-b", "b1"), c)

    def test_a_deletion_publishes_no_content(self):
        c = self.leak()
        subprocess.run(["/usr/bin/git", "push", "-q", "--no-verify", "origin", f"{c}:refs/heads/b1"], cwd=self.repo, env=self.env,
                       check=True, capture_output=True)
        self.ok("git", "import")
        self.jj("bookmark", "track", "b1@origin")
        self.ok("bookmark", "delete", "b1")
        r = self.jj("git", "push", "-b", "b1")
        self.assertEqual(r.returncode, 0, r.stderr)
        sink = dict(ln.split("\t") for ln in Path(f"{self.sinks}.log").read_text(encoding="utf-8").splitlines())[ROCQ]
        self.assertEqual(subprocess.run(["/usr/bin/git", "--git-dir", sink, "for-each-ref", "refs/heads/b1"], capture_output=True, text=True).stdout, "")

    def test_the_checked_commit_is_the_pushed_commit(self):
        # The check moves b1 to a leaking commit after it passes; git still sends the clean sha jj gave it. The
        # leaking commit is on a branch of its own: as an ancestor of the clean one it would be checked too.
        leaky = self.leak("other")
        self.ok("new", self.init)
        clean = self.commit("guard2.v", "(* clean *)", "Clean", "b1")
        mover = self.tmp / "check-then-move"
        mover.write_text(f'#!/bin/bash\n"{self.check}" "$@" || exit $?\n'
                         f'(cd "{self.repo}" && jj --ignore-working-copy bookmark set b1 --allow-backwards -r {leaky}) >&2\n',
                         encoding="utf-8")
        mover.chmod(0o755)
        self.env["PUBLIC_LEAK_CHECK"] = str(mover)
        r = self.jj("git", "push", "-b", "b1")
        self.assertIn("LEAK CHECK: clean", r.stderr)
        self.assertIn("Moved 1 bookmarks", r.stderr, "the mover did not run after the check")
        self.assertEqual(self.received(clean), [ROCQ])
        self.assertEqual(self.received(leaky), [])

    def test_a_plain_git_push_is_left_to_the_hook(self):
        c = self.leak()
        r = subprocess.run(["git", "push", "origin", f"{c}:refs/heads/x"], cwd=self.repo, env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("git shim:", r.stderr)
        self.assertNotIn("LEAK CHECK", r.stderr)
        self.assertEqual(self.received(c), [ROCQ])

    # A plain `git push` from a repository that sets its own core.hooksPath (dispatch://BET-706 R6, plan Slice 3): such
    # a repository's hooks directory overrides the global dispatcher, and no sweep may have put the leak-check wrapper
    # in it (a fresh clone of a project using husky or the pre-commit framework). In an agent session shims/git runs the
    # check first, through jj-push-hooks/pre-push, which then runs the repository's own pre-push.
    def count_checks(self) -> Path:
        """PUBLIC_LEAK_CHECK becomes a script that records each call, then runs the installed check; returns its log."""
        log = self.tmp / "check-calls.log"
        w = self.tmp / "counting-check"
        w.write_text(f'#!/bin/bash\nprintf \'%s\\n\' "$*" >> "{log}"\nexec bash "{self.check}" "$@"\n', encoding="utf-8")
        w.chmod(0o755)
        self.env["PUBLIC_LEAK_CHECK"] = str(w)
        return log

    @staticmethod
    def lines(path: Path) -> list[str]:
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    def recording_hook(self, hook: Path, label: str, status: int = 0, text: str = "") -> tuple[Path, Path, Path]:
        """An executable HOOK that records each call's arguments, its stdin byte for byte, and the two variables the
        gate could leak into it, under <tmp>/logs/LABEL.*, then exits STATUS. TEXT goes into its body (a marker)."""
        logs = self.tmp / "logs"
        logs.mkdir(exist_ok=True)
        calls, stdin, env = logs / f"{label}.calls", logs / f"{label}.stdin", logs / f"{label}.env"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text(f'#!/bin/bash\n# {text}\nprintf \'%s\\n\' "$*" >> "{calls}"\ncat >> "{stdin}"\n'
                        f'printf \'%s|%s\\n\' "${{GIT_CONFIG_PARAMETERS-}}" "${{GIT_SHIM_OWN_HOOKS-unset}}" >> "{env}"\nexit {status}\n',
                        encoding="utf-8")
        hook.chmod(0o755)
        return calls, stdin, env

    def leak_and_clean(self) -> tuple[str, str]:
        """A leaking commit on b1 and a clean one beside it (not its child, which the check would scan with it), both
        made before a test sets up any hook, so no jj snapshot takes a hook file into a commit."""
        leaky = self.leak()
        self.ok("new", self.init)
        return leaky, self.commit("clean.v", "(* clean *)", "Clean", "b2")

    def assert_plain_refused(self, r: subprocess.CompletedProcess[str], commit: str, url: str = ROCQ):
        self.assertNotEqual(r.returncode, 0, r.stderr)
        self.assertIn("LEAK CHECK: refusing to push", r.stderr)
        self.assertIn(f"error: git shim: the public leak check refused this push to {url}", r.stderr)
        self.assertNotIn(url, self.received(commit))

    def assert_own_hooks_path_is_checked_first(self, own: Path, commits: tuple[str, str]):
        """With the repository's own hooks directory OWN set up, a leak (the first of COMMITS) is refused with one
        check and the repository's pre-push not run; the clean one is pushed after one more check, and the repository's
        pre-push runs once, with git's arguments, git's stdin byte for byte, and none of the gate's configuration or
        variables."""
        leaky, clean = commits
        calls, stdin, env = self.recording_hook(own / "pre-push", "own")
        checks = self.count_checks()
        self.assert_plain_refused(self.git_push("origin", leaky, "x"), leaky)
        self.assertEqual(len(self.lines(checks)), 1, self.lines(checks))
        self.assertEqual(self.lines(calls), [], "the repository's own pre-push ran for a refused push")
        r = self.git_push("origin", clean, "y")
        self.assert_pushed(r, clean)
        self.assertIn("LEAK CHECK: clean", r.stderr)
        self.assertEqual(self.lines(checks), [f"origin {ROCQ}"] * 2)
        self.assertEqual(self.lines(calls), [f"origin {ROCQ}"])
        self.assertRegex(stdin.read_bytes(), rb"\A\S+ " + clean.encode() + rb" refs/heads/y 0{40}\n\Z")
        self.assertEqual(self.lines(env), ["|unset"], "the repository's pre-push saw the gate's -c or its variable")

    def test_a_plain_git_push_with_its_own_absolute_hooks_path_is_checked_first(self):
        commits = self.leak_and_clean()
        own = self.tmp / "own-hooks"
        self.git("config", "core.hooksPath", str(own))
        self.assert_own_hooks_path_is_checked_first(own, commits)

    def test_a_plain_git_push_with_a_husky_hooks_path_is_checked_first(self):
        # husky 9 sets `core.hooksPath=.husky/_`, relative: git takes it from the top of the work tree.
        commits = self.leak_and_clean()
        self.git("config", "core.hooksPath", ".husky/_")
        self.assert_own_hooks_path_is_checked_first(self.repo / ".husky" / "_", commits)

    def test_a_plain_git_push_with_a_home_relative_hooks_path_is_checked_first(self):
        commits = self.leak_and_clean()
        self.git("config", "core.hooksPath", "~/project-hooks")
        self.assert_own_hooks_path_is_checked_first(self.home / "project-hooks", commits)

    def test_a_plain_git_push_with_a_worktree_hooks_path_is_checked_first(self):
        commits = self.leak_and_clean()
        self.git("config", "extensions.worktreeConfig", "true")
        self.git("config", "--worktree", "core.hooksPath", str(self.tmp / "wt-hooks"))
        self.assert_own_hooks_path_is_checked_first(self.tmp / "wt-hooks", commits)

    def test_a_plain_git_push_refused_by_its_own_hook_after_the_check_is_refused(self):
        _, clean = self.leak_and_clean()
        own = self.tmp / "own-hooks"
        self.git("config", "core.hooksPath", str(own))
        calls, _, _ = self.recording_hook(own / "pre-push", "own", status=7)
        r = self.git_push("origin", clean, "y")
        self.assertNotEqual(r.returncode, 0, r.stderr)
        self.assertIn("LEAK CHECK: clean", r.stderr)
        self.assertEqual(self.lines(calls), [f"origin {ROCQ}"])
        self.assertEqual(self.received(clean), [])

    def test_a_plain_git_push_to_a_hooks_path_holding_the_wrapper_is_left_to_it(self):
        # The sweep's wrapper (marker PUBLIC-LEAK-CHECK-WRAPPER) runs the check itself; the shim adds no second one.
        leaky, _ = self.leak_and_clean()
        own = self.tmp / "own-hooks"
        self.git("config", "core.hooksPath", str(own))
        calls, _, _ = self.recording_hook(own / "pre-push", "wrapper", text="PUBLIC-LEAK-CHECK-WRAPPER stand-in")
        checks = self.count_checks()
        r = self.git_push("origin", leaky, "x")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.lines(checks), [])
        self.assertEqual(self.lines(calls), [f"origin {ROCQ}"])

    def test_a_plain_git_push_with_no_verify_is_the_users_choice(self):
        leaky, _ = self.leak_and_clean()
        own = self.tmp / "own-hooks"
        self.git("config", "core.hooksPath", str(own))
        calls, _, _ = self.recording_hook(own / "pre-push", "own")
        checks = self.count_checks()
        self.keep_cache_fresh()
        r = subprocess.run(["git", "push", "--no-verify", "origin", f"{leaky}:refs/heads/x"], cwd=self.repo, env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.lines(checks), self.lines(calls)), ([], []))

    def test_a_plain_git_push_from_an_unguarded_checkout_with_its_own_hooks_path_is_untouched(self):
        self.git("remote", "set-url", "origin", PRIVATE)
        leaky, _ = self.leak_and_clean()
        own = self.tmp / "own-hooks"
        self.git("config", "core.hooksPath", str(own))
        calls, _, env = self.recording_hook(own / "pre-push", "own")
        checks = self.count_checks()
        r = self.git_push("origin", leaky, "x")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.lines(checks), [])
        self.assertEqual(self.lines(calls), [f"origin {PRIVATE}"])
        self.assertEqual(self.lines(env), ["|unset"])

    # With the global dispatcher (dispatch://BET-706 R6) installed in HOME as provisioning installs it and HOME's git
    # config holding the dotfiles' include line, each push is checked once: by the dispatcher where the repository sets
    # no hooks path of its own, by the shim's gate where it does.
    def use_global_dispatcher(self) -> Path:
        self.env.pop("GIT_CONFIG_GLOBAL", None)
        install_global_hooks(self.home / GLOBAL_HOOKS, self.env)
        (self.home / ".gitconfig").write_text(dotfiles_include(), encoding="utf-8")
        self.assertEqual(self.git("config", "--global", "--includes", "--type=path", "--get", "core.hooksPath"),
                         str(self.home / GLOBAL_HOOKS / "global"))
        return self.home / GLOBAL_HOOKS / "global"

    def plain_push(self, cwd: Path, commit: str, ref: str, shim: bool = True) -> subprocess.CompletedProcess[str]:
        """`git push origin COMMIT:refs/heads/REF` in CWD, through shims/git, or through /usr/bin/git with no shim on PATH."""
        self.keep_cache_fresh()
        env = self.env if shim else {**self.env, "PATH": os.pathsep.join(p for p in self.env["PATH"].split(os.pathsep) if p != str(SHIMS))}
        return subprocess.run(["git" if shim else "/usr/bin/git", "push", "origin", f"{commit}:refs/heads/{ref}"],
                              cwd=cwd, env=env, capture_output=True, text=True)

    def test_with_the_global_dispatcher_a_fresh_clone_is_checked_once(self):
        # A fresh clone of a public repository: no sweep has hooked it, it has no hooks path, its own plain pre-push
        # runs after the check. Through shims/git and through /usr/bin/git with no shim on PATH alike.
        leaky, clean = self.leak_and_clean()
        clone = self.tmp / "clone"
        subprocess.run(["/usr/bin/git", "clone", "-q", str(self.repo), str(clone)], env={**self.env, "GIT_ALLOW_PROTOCOL": "file"}, check=True, capture_output=True)
        for k, v in (("remote.origin.url", ROCQ), ("remote.origin.vcs", "sink")):
            subprocess.run(["/usr/bin/git", "config", k, v], cwd=clone, env=self.env, check=True)
        calls, _, _ = self.recording_hook(clone / ".git" / "hooks" / "pre-push", "own")
        self.use_global_dispatcher()
        checks = self.count_checks()
        for n, shim in enumerate((True, False)):
            r = self.plain_push(clone, leaky, f"x{n}", shim)
            self.assertNotEqual(r.returncode, 0, r.stderr)
            self.assertIn("LEAK CHECK: refusing to push", r.stderr)
            self.assertNotIn(ROCQ, self.received(leaky))
            self.assertEqual(len(self.lines(checks)), 2 * n + 1, (shim, self.lines(checks)))
            self.assertEqual(len(self.lines(calls)), n, ("the clone's own pre-push ran for a refused push", shim))
            r = self.plain_push(clone, clean, f"y{n}", shim)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(len(self.lines(checks)), 2 * n + 2, (shim, self.lines(checks)))
            self.assertEqual(len(self.lines(calls)), n + 1, shim)
        self.assertIn(ROCQ, self.received(clean))

    def test_with_the_global_dispatcher_a_checkout_holding_the_wrapper_is_checked_once(self):
        # The sweep's wrapper, with the checkout's earlier hook kept as pre-push.local: the dispatcher runs the check,
        # then pre-push.local, and not the wrapper, so the check runs once and the local hook once.
        installer = global_hooks_source() / "install-prepush-hook.sh"
        if not installer.is_file():
            self.skipTest(f"no install-prepush-hook.sh beside {installer.parent}/install-global-hooks.sh")
        leaky, clean = self.leak_and_clean()
        hooks = self.repo / ".git" / "hooks"
        calls, stdin, _ = self.recording_hook(hooks / "pre-push", "local")
        self.env["XDG_CONFIG_HOME"] = str(self.tmp / "config")
        subprocess.run([str(installer), str(self.repo)], cwd=self.tmp, env=self.env, check=True, capture_output=True)
        self.assertIn("PUBLIC-LEAK-CHECK-WRAPPER", (hooks / "pre-push").read_text(encoding="utf-8"))
        self.assertTrue(os.access(hooks / "pre-push.local", os.X_OK))
        self.use_global_dispatcher()
        checks = self.count_checks()
        r = self.git_push("origin", leaky, "x")
        self.assertNotEqual(r.returncode, 0, r.stderr)
        self.assertIn("LEAK CHECK: refusing to push", r.stderr)
        self.assertEqual((len(self.lines(checks)), self.lines(calls)), (1, []))
        r = self.git_push("origin", clean, "y")
        self.assert_pushed(r, clean)
        self.assertEqual(len(self.lines(checks)), 2, self.lines(checks))
        self.assertEqual(self.lines(calls), [f"origin {ROCQ}"])
        self.assertRegex(stdin.read_bytes(), rb"\A\S+ " + clean.encode() + rb" refs/heads/y 0{40}\n\Z")

    def test_with_the_global_dispatcher_a_husky_hooks_path_is_checked_once(self):
        # The repository's own hooks path wins over the global one, so the dispatcher plays no part; the shim's gate
        # checks once.
        commits = self.leak_and_clean()
        self.git("config", "core.hooksPath", ".husky/_")
        self.use_global_dispatcher()
        self.assert_own_hooks_path_is_checked_first(self.repo / ".husky" / "_", commits)

    # Round 4 (cross-family review lanes/leakgate/slice3-xf-dotfiles.md): two own-hooks-path states in which neither
    # the dispatcher nor the shim ran the check. Both with the global dispatcher installed, as on sami-devbox.
    def installed_wrapper_in(self, hooks: Path) -> tuple[Path, Path]:
        """The real install-prepush-hook.sh run on this checkout, its earlier hook in HOOKS kept as pre-push.local;
        returns that hook's call log and the wrapper."""
        installer = global_hooks_source() / "install-prepush-hook.sh"
        if not installer.is_file():
            self.skipTest(f"no install-prepush-hook.sh beside {installer.parent}/install-global-hooks.sh")
        calls, _, _ = self.recording_hook(hooks / "pre-push", "local")
        self.env["XDG_CONFIG_HOME"] = str(self.tmp / "config")
        subprocess.run([str(installer), str(self.repo)], cwd=self.tmp, env=self.env, check=True, capture_output=True)
        self.assertIn("PUBLIC-LEAK-CHECK-WRAPPER", (hooks / "pre-push").read_text(encoding="utf-8"))
        self.assertTrue(os.access(hooks / "pre-push.local", os.X_OK))
        return calls, hooks / "pre-push"

    def assert_checked_once_each(self, leaky: str, clean: str, cwd: Path | None = None, shim: bool = True) -> Path:
        """A leak refused after one check and nothing of it at the receiver, then a clean commit sent after one more."""
        checks = self.count_checks()
        checks.unlink(missing_ok=True)
        r = self.plain_push(cwd or self.repo, leaky, "x", shim)
        self.assertNotEqual(r.returncode, 0, r.stderr)
        self.assertIn("LEAK CHECK: refusing to push", r.stderr)
        self.assertNotIn(ROCQ, self.received(leaky))
        self.assertEqual(len(self.lines(checks)), 1, self.lines(checks))
        r = self.plain_push(cwd or self.repo, clean, "y", shim)
        self.assert_pushed(r, clean)
        self.assertEqual(len(self.lines(checks)), 2, self.lines(checks))
        return checks

    def test_a_non_executable_wrapper_in_its_own_hooks_path_is_checked_by_the_shim(self):
        # git ignores a hook that is not executable ("hook was ignored because it's not set as executable"), so a
        # wrapper with its execute bits off checks nothing; the shim must not count it as present. pre-push.local does
        # not run either, as git would not run it without the wrapper.
        leaky, clean = self.leak_and_clean()
        own = self.tmp / "own-hooks"
        self.git("config", "core.hooksPath", str(own))
        calls, wrapper = self.installed_wrapper_in(own)
        self.use_global_dispatcher()
        wrapper.chmod(0o644)
        self.assert_checked_once_each(leaky, clean)
        self.assertEqual(self.lines(calls), [])

    def test_an_executable_wrapper_in_its_own_hooks_path_still_checks_alone(self):
        # The control: the same real wrapper, executable, runs the check once and then pre-push.local; the shim adds
        # nothing.
        leaky, clean = self.leak_and_clean()
        own = self.tmp / "own-hooks"
        self.git("config", "core.hooksPath", str(own))
        calls, _ = self.installed_wrapper_in(own)
        self.use_global_dispatcher()
        self.assert_checked_once_each(leaky, clean)
        self.assertEqual(self.lines(calls), [f"origin {ROCQ}"])

    def test_with_the_global_dispatcher_a_non_executable_wrapper_is_checked_once(self):
        # The dispatcher's half of the same shape, in a checkout with no hooks path of its own: it runs the check, and
        # skips the wrapper only when it is executable (as git would run it), so a non-executable one changes nothing
        # but that neither it nor pre-push.local runs. Through shims/git and through /usr/bin/git alike.
        leaky, clean = self.leak_and_clean()
        calls, wrapper = self.installed_wrapper_in(self.repo / ".git" / "hooks")
        self.use_global_dispatcher()
        wrapper.chmod(0o644)
        for shim in (True, False):
            self.assert_checked_once_each(leaky, clean, shim=shim)
            subprocess.run(["/usr/bin/git", "--git-dir", str(self.sink_of(ROCQ)), "update-ref", "-d", "refs/heads/y"], check=True)
        self.assertEqual(self.lines(calls), [])

    def assert_empty_hooks_path_is_checked(self, *scope: str):
        # git 2.43 with core.hooksPath set to "" in the repository's own config: the value overrides the global
        # dispatcher, and git looks for each hook at /<name> (`git rev-parse --git-path hooks/pre-push` prints
        # /pre-push), so in practice no hook runs. The shim treats that directory, `/`, as any own hooks path without
        # the wrapper, and runs the check.
        leaky, clean = self.leak_and_clean()
        self.git("config", *scope, "core.hooksPath", "")
        self.use_global_dispatcher()
        self.assertEqual(self.git("rev-parse", "--git-path", "hooks/pre-push"), "/pre-push")
        self.assert_checked_once_each(leaky, clean)

    def test_an_empty_local_hooks_path_is_checked_by_the_shim(self):
        self.assert_empty_hooks_path_is_checked("--local")

    def test_an_empty_worktree_hooks_path_is_checked_by_the_shim(self):
        self.git("config", "extensions.worktreeConfig", "true")
        self.assert_empty_hooks_path_is_checked("--worktree")

    def test_other_commands_are_untouched(self):
        self.leak()
        (self.repo / "untracked.txt").write_text("u\n", encoding="utf-8")
        self.calls.unlink(missing_ok=True)
        via = subprocess.run(["git", "status", "--porcelain"], cwd=self.repo, env=self.env, capture_output=True, text=True)
        direct = subprocess.run(["/usr/bin/git", "status", "--porcelain"], cwd=self.repo, env=self.env, capture_output=True, text=True)
        self.assertEqual((via.returncode, via.stdout, via.stderr), (direct.returncode, direct.stdout, direct.stderr))
        self.assertEqual(self.calls.read_text(encoding="utf-8").splitlines(), ["status --porcelain"], "git status must reach git once, unchanged")
        self.calls.unlink()
        argv = ["log", "--no-graph", "-r", "::@", "-T", "commit_id ++ ' ' ++ description.first_line() ++ '\\n'"]
        via = self.jj(*argv)
        direct = run_bounded([str(real_jj()), *argv], self.repo, self.env)
        self.assertEqual((via.returncode, via.stdout), (direct.returncode, direct.stdout))
        self.assertFalse(self.calls.exists(), "jj log must run no git")
        # Cost: CPU time (which the box's load barely moves, unlike wall time) of `git status` through this shim and
        # through the same shim with the jj push gate cut out, both copied under one directory, alternating.
        text = (SHIMS / "git").read_text(encoding="utf-8")
        start = text.index("# jj publishes through git")
        tail = '    exec "$_real" "$@"\nfi\n'
        end = text.index(tail, start) + len(tail)
        shims = {"with the gate": text, "without it": text[:start] + text[end:]}
        self.assertNotIn("jj_push_gate", shims["without it"])
        cpu: dict[str, list[float]] = {}
        for name, body in shims.items():
            path = self.tmp / name.replace(" ", "-") / "git"
            path.parent.mkdir()
            path.write_text(body, encoding="utf-8")
            path.chmod(0o755)
            shims[name] = str(path)
            cpu[name] = []
        for k in range(60):
            for name in (("with the gate", "without it") if k % 2 == 0 else ("without it", "with the gate")):
                before = resource.getrusage(resource.RUSAGE_CHILDREN)
                subprocess.run([shims[name], "status", "--porcelain"], cwd=self.repo, env=self.env, capture_output=True, check=True)
                after = resource.getrusage(resource.RUSAGE_CHILDREN)
                cpu[name].append(after.ru_utime - before.ru_utime + after.ru_stime - before.ru_stime)
        added = statistics.median(cpu["with the gate"]) - statistics.median(cpu["without it"])
        print(f"\n  git status CPU: with the gate {statistics.median(cpu['with the gate']) * 1000:.2f} ms, "
              f"without it {statistics.median(cpu['without it']) * 1000:.2f} ms")
        self.assertLess(added, 0.001, "the jj push gate adds a millisecond or more of CPU to git status")

    # Fail closed, with a message that says what failed.
    def test_no_check_on_the_machine_refuses(self):
        c = self.commit("guard.v", "(* clean *)", "Clean", "b1")
        self.env["PUBLIC_LEAK_CHECK"] = ""
        self.env["XDG_CONFIG_HOME"] = str(self.tmp / "empty-config")
        self.env["PATH"] = os.pathsep.join(p for p in self.env["PATH"].split(os.pathsep) if not (Path(p) / "public-leak-check.sh").exists())
        for argv in (["git", "push", "-b", "b1"], ["--quiet", "git", "push", "-b", "b1"]):
            r = self.jj(*argv)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("error: git shim: this machine has no public leak check", r.stderr)
            self.assertIn("Nothing is wrong with your commits", r.stderr)
        self.assertEqual(self.received(c), [])

    def test_a_check_that_cannot_finish_refuses(self):
        c = self.commit("guard.v", "(* clean *)", "Clean", "b1")
        broken = self.tmp / "broken-check"
        broken.write_text("#!/bin/sh\necho 'LEAK CHECK ERROR: injected' >&2\nexit 2\n", encoding="utf-8")
        broken.chmod(0o755)
        self.env["PUBLIC_LEAK_CHECK"] = str(broken)
        r = self.jj("--quiet", "git", "push", "-b", "b1")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("error: LEAK CHECK ERROR: injected", r.stderr)
        self.assertIn("the public leak check could not finish (exit 2)", r.stderr)
        self.assertEqual(self.received(c), [])

    def test_a_missing_gate_hook_refuses(self):
        shims = self.tmp / "shims-copy"
        shutil.copytree(SHIMS, shims, symlinks=True, ignore=shutil.ignore_patterns("tests", "__pycache__"))
        (shims / "jj-push-hooks" / "pre-push").unlink()
        self.env["PATH"] = self.path_with(shims)
        c = self.leak()
        self.assert_refused_unread(self.jj("--quiet", "git", "push", "-b", "b1"), c, "the leak gate's hook")

    def test_a_visibility_failure_is_not_called_leaked_text(self):
        # Neither the gate nor the check finds a cache entry for theorem-labs/rocq, and the fake gh answers neither, so
        # the gate guards the checkout (dispatch://BET-706 R4) and the check refuses for that reason alone, naming the
        # repository as the URL writes it.
        url = "https://github.com/Theorem-Labs/ROCQ.git"
        self.git("remote", "set-url", "origin", url)
        (self.cache / "theorem-labs_rocq").unlink()
        c = self.commit("guard.v", "(* clean *)", "Clean", "b1")
        self.assert_refused_unreadable(self.jj("git", "push", "-b", "b1"), c, url, "Theorem-Labs/ROCQ")
        self.assertEqual(self.received(c), [])
        self.assertTrue(self.gh_calls(), "the visibility read went to the fake gh")

    def test_a_jj_binary_replaced_while_it_runs_is_still_jj(self):
        # mise replacing jj under a running jj leaves /proc/<pid>/exe reading "…/jj (deleted)".
        copy = self.tmp / "jjcopy"
        copy.mkdir()
        shutil.copy2(real_jj(), copy / "jj")
        w = self.git_wrapper("git-deleting-jj", 'rm -f -- "$(readlink /proc/$PPID/exe)"; args=("$@")')
        c = self.leak()
        r = run_bounded([str(copy / "jj"), "--config", f"git.executable-path={w}", "git", "push", "-b", "b1"], self.repo, self.env)
        self.assertFalse((copy / "jj").exists(), "the wrapper did not delete the running jj")
        self.assert_refused(r, c)


class GlobalHooksIncludeTest(unittest.TestCase):
    """The dotfiles' .gitconfig, as a machine's global config, turns the dispatcher on where the install wrote
    global.gitconfig, and changes nothing where it did not: git ignores an include whose file is missing."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.env = {**{k: v for k, v in os.environ.items() if not k.startswith("GIT_CONFIG")},
                    "HOME": str(self.home), "GIT_CONFIG_GLOBAL": str(DOTFILES_GITCONFIG), "GIT_CONFIG_NOSYSTEM": "1"}
        subprocess.run(["/usr/bin/git", "init", "-q", str(self.tmp / "repo")], env=self.env, check=True)

    def hooks_path(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["/usr/bin/git", "config", "--show-origin", "--type=path", "--get", "core.hooksPath"],
                              cwd=self.tmp / "repo", env=self.env, capture_output=True, text=True)

    def test_the_include_turns_the_dispatcher_on_only_where_it_is_installed(self):
        r = self.hooks_path()
        self.assertEqual((r.returncode, r.stdout, r.stderr), (1, "", ""), "a machine without the install must be unchanged")
        self.assertEqual(subprocess.run(["/usr/bin/git", "status", "--porcelain"], cwd=self.tmp / "repo", env=self.env).returncode, 0)
        gc = self.home / GLOBAL_HOOKS / "global.gitconfig"
        gc.parent.mkdir(parents=True)
        gc.write_text(f"[core]\n\thooksPath = ~/{GLOBAL_HOOKS}/global\n", encoding="utf-8")
        r = self.hooks_path()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, f"file:{gc}\t{self.home / GLOBAL_HOOKS / 'global'}\n")


class CustomerCanaryTest(unittest.TestCase):
    """customer_name reads the canary where each kind of installed check keeps it. The name here is made up."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def write(self, name: str, text: str) -> Path:
        p = self.tmp / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def test_the_shell_check_keeps_it_in_a_self_test_line(self):
        check = self.write("public-leak-check.sh", "#!/bin/bash\nSELFTEST='\ncustomer|graded on Examplecorp, v1\n'\n")
        self.assertEqual(customer_name(check), "Examplecorp")

    def test_the_python_check_keeps_it_in_its_canary_generator_beside_the_launcher(self):
        check = self.write("v8/public-leak-check.sh", '#!/usr/bin/env bash\nCHECK=$(dirname "$SELF")/public_leak_check.py\n')
        self.write("v8/public_leak_check.py", '    (1, (0,), "customer-platform", b"graded on Examplecorp, problem version " + draw(12)),\n')
        self.assertEqual(customer_name(check), "Examplecorp")

    def test_a_launcher_reached_through_a_link_reads_the_python_check_beside_its_target(self):
        self.write("v8/public-leak-check.sh", "#!/usr/bin/env bash\n")
        self.write("v8/public_leak_check.py", '(1, (0,), "customer-platform", b"graded on Examplecorp, problem version "),\n')
        (self.tmp / "bin").mkdir()
        (self.tmp / "bin" / "public-leak-check.sh").symlink_to(self.tmp / "v8" / "public-leak-check.sh")
        self.assertEqual(customer_name(self.tmp / "bin" / "public-leak-check.sh"), "Examplecorp")

    def test_a_check_with_no_canary_names_the_file_it_read(self):
        check = self.write("v8/public-leak-check.sh", "#!/usr/bin/env bash\n")
        py = self.write("v8/public_leak_check.py", "CANARIES = []\n")
        with self.assertRaisesRegex(AssertionError, re.escape(str(py))):
            customer_name(check)



if __name__ == "__main__":
    unittest.main()
