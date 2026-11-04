#!/usr/bin/env python3
"""shims/git: a `git push` that jj runs gets the shared public leak check wherever git's pre-push hook would give it.

jj runs git with --no-verify, so no hook runs for a jj push. In a guarded checkout shims/git drops that flag and points
core.hooksPath at shims/jj-push-hooks/, whose pre-push makes the installed hook's call to the check, with git's own URL
and update lines. The real jj and the real git do every step here, with this shims dir ahead of every other git and jj
on PATH. Most remotes are reached through a remote helper (`remote.<name>.vcs=sink`, `git-remote-sink` below): git
keeps the URL as written, hands it to the hook, and the helper connects git to a local bare repository named after that
URL. The cases that need git's own transports use a real local path, or ssh through a local-only stand-in
(`SSH_ADAPTER`). GIT_ALLOW_PROTOCOL allows only those, and a fake gh, first on PATH (ahead of shims/gh too), answers
nothing. So no test can reach GitHub, even when the gate wrongly lets a push through, and each sink shows exactly which
commit reached which URL. The check is the installed one, found the way the shim and the pre-push hook find it; HOME is
the test's own, so the visibility cache is the test's.
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


def customer_name(check: Path) -> str:
    """The customer's name, read from the check's own self-test canary (`customer|graded on <name>, ...`). This file
    lands in a public repository, so it never names the customer; the leaking text is built from this at run time."""
    m = re.search(r"^customer\|graded on ([A-Za-z]+),", check.read_text(encoding="utf-8"), re.M)
    if not m:
        raise AssertionError(f"{check} has no `customer|graded on <name>,` self-test canary to build the leaking text from")
    return m.group(1)


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

# Answers no gh call, and records each one: the check reads a repository's visibility through gh when its cache has
# no answer, and no test may send that read to GitHub.
FAKE_GH = '#!/bin/bash\nprintf \'%s\\n\' "$*" >> "@GHLOG@"\nexit 1\n'

# The leak check's visibility cache as each test starts: the six theorem-labs repositories this box's cache records as
# public (2026-11-01), one outside the hook sweeper's six that it records as public, a private one, and public
# repositories of other owners. The check decides every target from these entries; it lower-cases a slug before it
# reads one. The %72ocq entry is for the gate, whose rule reads the cache by the path as written: the check calls
# theorem-labs/%72ocq no repository name and refuses a push to it unread (test_other_names_and_url_forms).
CACHE = {
    **{f"theorem-labs_{r}": "public" for r in ("rocq", "rocq-linux-process-semantics", "oss-verify", "metarocq", "legion", "oss-verify-proofs")},
    "theorem-labs_cached-public": "public",
    "theorem-labs_%72ocq": "public",
    "theorem-labs_private-fixture": "private",
    # sjawhar/dotfiles is public and, since its scrub (dispatch://INFRA-974), the sweeper's `others`: checked like any
    # other public target.
    "sjawhar_dotfiles": "public",
    "sjawhar_rocq": "public",
    "sjawhar_legion": "public",
    "rocq-prover_rocq": "public",
}

# Guarded or not, under CACHE, by the hook sweeper's `git remote -v` rule: a line matching
# theorem-labs/(<names>)(\.git)?/?[[:space:]], letter case ignored. (want, remote name, URL, how the config holds it:
# "" as `git remote add` writes it, "quoted" with the URL in double quotes, or "insteadOf" for a URL that
# url.https://github.com/theorem-labs/.insteadOf=r3: rewrites, as in the revision-3 review's R3-2.)
GUARD_TABLE = [
    ("Y", "origin", "https://github.com/theorem-labs/rocq.git", ""),
    ("Y", "origin", "https://github.com/theorem-labs/rocq", ""),
    ("Y", "origin", "git@github.com:theorem-labs/rocq.git", ""),
    ("Y", "origin", "git@github.com:/theorem-labs/rocq.git", ""),
    ("Y", "origin", "ssh://git@ssh.github.com:443/theorem-labs/rocq.git", ""),
    ("Y", "origin", "https://x-access-token:not-a-secret@github.com/theorem-labs/rocq.git", ""),
    ("Y", "origin", "https://github.com//theorem-labs/rocq.git", ""),
    ("Y", "origin", "https::https://github.com/theorem-labs/rocq", ""),
    ("Y", "origin", "git@alias-xf:theorem-labs/rocq.git", ""),
    ("Y", "origin", "git@UPPERALIAS:theorem-labs/rocq.git", ""),
    ("Y", "origin", "useralias:theorem-labs/rocq", ""),
    # The revision-2 review's R2-5 and R2-2: an alias with # or ?, and a path with a space, read whole.
    ("Y", "origin", "git@alias#xf:theorem-labs/rocq.git", ""),
    ("Y", "origin", "git@alias?xf:theorem-labs/rocq.git", ""),
    ("Y", "origin", "git@corp.example:mirror space/theorem-labs/rocq.git", ""),
    ("Y", "origin", "/tmp/mirror space/theorem-labs/rocq.git", ""),
    ("Y", "origin", "https://notgithub.com/theorem-labs/rocq.git", ""),
    ("Y", "origin", "file:///srv/theorem-labs/rocq.git", ""),
    ("Y", "origin", "https://example.com/not-theorem-labs/rocq.git", ""),
    ("Y", "origin", "git+ssh://git@github.com/theorem-labs/rocq-linux-process-semantics", ""),
    ("Y", "origin", "ssh+git://github.com/theorem-labs/oss-verify.git", ""),
    ("Y", "origin", "git@github.com:theorem-labs/oss-verify-proofs.git", ""),
    ("Y", "origin", "https://github.com/theorem-labs/legion", ""),
    ("Y", "origin", "https://github.com/theorem-labs/metarocq.git", ""),
    ("Y", "origin", "https://github.com/theorem-labs/cached-public.git", ""),
    ("Y", "origin", "https://github.com/theorem-labs/%72ocq.git", ""),
    # Letter case and a trailing slash (the sweeper's rule since 2026-11-02 04:45Z).
    ("Y", "origin", "HTTPS://GitHub.COM/Theorem-Labs/Rocq.GIT", ""),
    ("Y", "origin", "Git@GitHub.com:Theorem-Labs/Rocq.git", ""),
    ("Y", "origin", "https://github.com/Theorem-Labs/Cached-Public.git", ""),
    ("Y", "origin", "https://github.com/theorem-labs/rocq/", ""),
    ("Y", "origin", "https://github.com/theorem-labs/rocq.git/", ""),
    # The revision-3 review's R3-2: a quoted URL, and one an insteadOf rewrites.
    ("Y", "origin", "https://github.com/theorem-labs/rocq.git", "quoted"),
    ("Y", "origin", "r3:rocq.git", "insteadOf"),
    ("Y", "theorem-labs/rocq", "https://example.com/elsewhere.git", ""),
    ("N", "origin", "https://github.com/theorem-labs/private-fixture.git", ""),
    ("N", "origin", "https://github.com/theorem-labs/never-cached.git", ""),
    ("N", "origin", "https://github.com/theorem-labs/oss-verify-proofs-internal.git", ""),
    ("N", "origin", "https://github.com/theorem-labs/./rocq.git", ""),
    ("N", "origin", "https://github.com/theorem-labs/x/../rocq.git", ""),
    ("N", "origin", "https://github.com/theorem-labs///rocq.git", ""),
    ("N", "origin", "https://github.com/theorem-labs/rocq.git?x=1", ""),
    ("N", "origin", "https://github.com/theorem-labs/rocq//", ""),
    ("N", "origin", "https://github.com/theorem-labs/rocq/extra", ""),
    ("N", "origin", "https://github.com/theorem-labs/.git", ""),
    ("N", "origin", "https://github.com/sjawhar/rocq", ""),
    ("N", "origin", "https://github.com/rocq-prover/rocq", ""),
    # The sweeper's `others`: sjawhar/dotfiles, whatever the host, letter case as for the rest.
    ("Y", "origin", "https://github.com/sjawhar/dotfiles.git", ""),
    ("Y", "origin", "git@github.com:SJawhar/Dotfiles.git", ""),
    ("Y", "origin", "https://github.com/sjawhar/dotfiles/", ""),
    ("N", "origin", "https://github.com/sjawhar/dotfiles-public", ""),
    ("N", "origin", "../a.git", ""),
    ("N", "theorem-labs", "origin", ""),
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
        for f in (*fake.iterdir(), self.ghdir / "gh"):
            f.chmod(0o755)
        # The gate reads the cache where the hook sweeper does, ~/.cache/public-leak-check, and the check where
        # XDG_CACHE_HOME says: one directory here, as on this box, where XDG_CACHE_HOME is unset.
        self.home = self.tmp / "home"
        self.cache = self.home / ".cache" / "public-leak-check"
        self.cache.mkdir(parents=True)
        for name, word in CACHE.items():
            (self.cache / name).write_text(word, encoding="utf-8")
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
        }
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
        return self.ghlog.read_text(encoding="utf-8").splitlines() if self.ghlog.exists() else []

    def assert_refused(self, r: subprocess.CompletedProcess[str], commit: str, url: str = ROCQ):
        """Refused by the check, with the commit and the matching text in lines jj shows even under --quiet, and
        nothing of COMMIT received at URL."""
        self.assertNotEqual(r.returncode, 0, r.stderr)
        err = re.sub(r"\x1b\[[0-9;]*m", "", r.stderr)
        self.assertIn("LEAK CHECK: refusing to push", err)
        self.assertIn(f"error: git shim: the public leak check refused this jj push to {url}", err)
        shown = [ln for ln in err.splitlines() if ln.startswith("error: ")]
        self.assertTrue([ln for ln in shown if commit[:7] in ln], ("the refusal must name the commit", r.stderr))
        self.assertTrue([ln for ln in shown if f"[customer: {self.customer}]" in ln], ("the refusal must name what matched", r.stderr))
        self.assertNotIn(url, self.received(commit))
        self.assertEqual(self.gh_calls(), [])

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
        # The reviewer's three aliases (lanes/jj-gate-review-xf-2026-11-02.md, B2), each the checkout's only remote: a
        # config file given through GIT_SSH_COMMAND -F, an upper-case Host, a Match on the user. The checkout is guarded
        # by its path theorem-labs/rocq, as the hook sweeper guards it, whatever ssh would resolve the host to.
        cfg = self.tmp / "sshconfig"
        cfg.write_text("Host alias-xf\n  HostName github.com\nHost UPPERALIAS\n  HostName github.com\n"
                       "Match host useralias user git\n  HostName github.com\n", encoding="utf-8")
        self.env["GIT_SSH_COMMAND"] = f"false -F {cfg}"
        c = self.leak()
        for url in ("git@alias-xf:theorem-labs/rocq.git", "git@UPPERALIAS:theorem-labs/rocq.git", "git@useralias:theorem-labs/rocq.git"):
            self.git("remote", "set-url", "origin", url)
            r = self.jj("git", "push", "-b", "b1")
            self.assert_refused(r, c, url)
        self.assertEqual(self.received(c), [])

    def test_guarded_is_the_sweepers_rule(self):
        # Finding 6 of the revision-2 review, and the revision-3 review's R3-2: the gate's rule is the hook sweeper's
        # `git remote -v` test, copied. Each GUARD_TABLE row is a real checkout. The gate's own functions judge its
        # `git remote -v`; when sweep-hooks.sh is beside the check, the sweeper's own code, taken out of that file as it
        # is at this run, then makes its whole decision for the same checkout: its config-file prefilter, then its
        # `git remote -v` test. The gate must give the table's answer and the same answer as the sweeper's `git remote
        # -v` test, so a change to that rule fails here until the gate follows. The prefilter only narrows: the
        # sweeper must never install a hook where the gate does not check, and where the gate checks a push the
        # sweeper left without a hook, the check still decides, so the gate refuses there only leaking content.
        env = {k: v for k, v in self.env.items() if not k.startswith("GIT_CONFIG")} | {"GIT_CONFIG_NOSYSTEM": "1"}
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
            checkouts.append(str(co))

        def judge(script: str) -> list[str]:
            r = subprocess.run(["bash", "-c", script, "judge", *checkouts], env=env, capture_output=True, text=True, check=True)
            return r.stdout.split()

        want = [w for w, *_ in GUARD_TABLE]
        rows = [f"{name} {url} {how}".strip() for _, name, url, how in GUARD_TABLE]
        gate = judge(f"eval \"$(awk '/^jj_push_refuse\\(\\)/,/^jj_push_gate\\(\\)/' '{SHIMS / 'git'}' | sed '$d')\"\n"
                     'for co in "$@"; do if jj_push_guarded "$(/usr/bin/git -C "$co" remote -v)"; then echo Y; else echo N; fi; done\n')
        self.assertEqual(list(zip(gate, rows)), list(zip(want, rows)))
        sweeper = self.check.parent / "sweep-hooks.sh"
        if not sweeper.is_file():
            self.skipTest("no sweep-hooks.sh beside the check to compare with")
        text = sweeper.read_text(encoding="utf-8")
        parts = {
            "public_names": re.search(r"^public_names\(\) \{\n.*?^\}$", text, re.M | re.S),
            "config_of": re.search(r"^config_of\(\) \{\n.*?^\}$", text, re.M | re.S),
            "repos": re.search(r'^\s*repos=".*"$', text, re.M),
            "cfgre": re.search(r'^\s*cfgre=".*"$', text, re.M),
            "the prefilter": re.search(r'^\s*if (\[ -n "\$cfg" \] .*); then continue; fi$', text, re.M),
            "the remote -v test": re.search(r'^\s*(git -C "\$co" remote -v .*) \|\| continue$', text, re.M),
        }
        missing = [k for k, m in parts.items() if not m]
        self.assertEqual(missing, [], f"{sweeper} no longer holds these parts of the rule the gate copies: read it again")
        p = {k: m.group(1 if m.re.groups else 0).strip() for k, m in parts.items()}
        # Every fixed list the sweeper's rule names (a top-level `NAME='…'` it uses: base, others) must be the gate's
        # JJ_GATE_<NAME>, so a name added to the sweeper's lists fails here even with no table row for it.
        fixed = dict(re.findall(r"^(\w+)='([^'\n]*)'$", text, re.M))
        used = {v for v in re.findall(r"\$(\w+)", p["repos"] + p["cfgre"] + p["public_names"]) if v in fixed}
        shim = (SHIMS / "git").read_text(encoding="utf-8")
        self.assertEqual({v: fixed[v] for v in used},
                         {v: (m.group(1) if (m := re.search(rf"^JJ_GATE_{v.upper()}='([^'\n]*)'$", shim, re.M)) else None) for v in used},
                         "the sweeper's fixed lists are not the gate's")
        self.assertIn("base", used)
        own = judge("\n".join(["set -u", *(f"{v}='{fixed[v]}'" for v in sorted(used)), p["public_names"], p["config_of"],
                               "names=$(public_names)", p["repos"], p["cfgre"],
                               'for co in "$@"; do',
                               f'  if {p["the remote -v test"]}; then r=Y; else r=N; fi',
                               '  cfg=$(config_of "$co/.git" "$co")',
                               f'  if {p["the prefilter"]}; then s=N; else s=$r; fi',
                               '  echo "$r$s"',
                               "done", ""]))
        self.assertEqual(list(zip([o[0] for o in own], rows)), list(zip(want, rows)), "the gate is not the sweeper's `git remote -v` test")
        self.assertEqual([row for g, o, row in zip(gate, own, rows) if o[1] == "Y" and g == "N"], [], "the sweeper installs a hook where the gate does not check")
        more = [row for g, o, row in zip(gate, own, rows) if g == "Y" and o[1] == "N"]
        print(f"\n  the gate checks, the sweeper installs no hook: {more}")

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
        # any other: the sweeper hooks a checkout of it (its `others`), and the gate checks a jj push from one. Revisions
        # 1 to 3 left every push to it alone, pending Sami's answer (dispatch://BET-214/ask/fd494519).
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

    def test_an_unguarded_checkout_is_untouched(self):
        # No remote names one of the sweeper's six or a theorem-labs repository the cache calls public: the sweeper
        # installs no hook, nor does the gate check, whatever the targets and the content. git runs exactly as jj
        # asked: --no-verify, no hooksPath.
        self.git("remote", "set-url", "origin", PRIVATE)
        targets = {"origin": PRIVATE, "legion": "https://github.com/sjawhar/legion.git",
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

    def test_the_cache_decides_and_an_unreadable_one_refuses(self):
        # Outside the sweeper's six, its rule reads the check's cache at every push: an entry theorem-labs_<repo>
        # holding exactly `public` guards the checkout; none, `private` or `internal` does not, nor does a mixed-case
        # file name, which the sweeper's case-sensitive glob skips, so it installs no hook there either. Where the
        # answer depends on the cache and the cache cannot give it (an entry that is empty, unreadable or holds a word
        # the check never writes, or a cache directory that cannot be read: the reviewer's B1, cases 1 and 2), the
        # push is refused unread; an undecided entry of another repository changes nothing.
        url = "https://github.com/theorem-labs/newly-public.git"
        self.git("remote", "set-url", "origin", url)
        entry = self.cache / "theorem-labs_newly-public"
        n = 0

        def attempt(quiet: bool = False) -> tuple[str, subprocess.CompletedProcess[str]]:
            nonlocal n
            n += 1
            c = self.commit(f"g{n}.v", self.leak_text, f"Leak {n}", "b1")
            return c, self.jj(*(["--quiet"] if quiet else []), "git", "push", "-b", "b1")

        c, r = attempt()
        self.assert_pushed(r, c, url)  # no entry: not known public, as on a machine without the cache
        for word in ("private", "internal"):
            entry.write_text(word, encoding="utf-8")
            c, r = attempt()
            self.assert_pushed(r, c, url)
        (self.cache / "theorem-labs_other").write_text("", encoding="utf-8")
        c, r = attempt()
        self.assert_pushed(r, c, url)  # the empty entry is another repository's
        for word in ("public", "public\n"):
            entry.write_text(word, encoding="utf-8")
            c, r = attempt()
            self.assert_refused(r, c, url)
        entry.unlink()
        (self.cache / "Theorem-Labs_Newly-Public").write_text("public", encoding="utf-8")
        c, r = attempt()
        self.assert_pushed(r, c, url)
        self.assertNotIn("LEAK CHECK", r.stderr)
        (self.cache / "Theorem-Labs_Newly-Public").unlink()
        for word in ("", "Unknown"):
            entry.write_text(word, encoding="utf-8")
            c, r = attempt(quiet=True)
            self.assert_refused_unread(r, c, f"the public leak check's visibility cache entry {entry} is empty, unreadable")
        entry.write_text("public", encoding="utf-8")
        entry.chmod(0)
        c, r = attempt(quiet=True)
        self.assert_refused_unread(r, c, f"the public leak check's visibility cache entry {entry} is empty, unreadable")
        entry.chmod(0o644)
        self.cache.chmod(0)
        c, r = attempt(quiet=True)
        self.cache.chmod(0o755)
        self.assert_refused_unread(r, c, "cannot read the public leak check's visibility cache")

    def test_a_cache_entry_rewritten_during_the_push_refuses(self):
        # The reviewer's B1 case 3: the entry goes empty while the gate lists the remotes, as an entry the check is
        # rewriting in place does, and is written again before git pushes. The sweeper's six need no entry, so this is
        # a public repository outside them.
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
        self.assert_refused_unread(self.jj("git", "push", "-b", "b1"), c, "the public leak check's visibility cache entry")
        self.assertEqual(entry.read_text(encoding="utf-8"), "", "the race did not run as staged")

    def test_an_unsearchable_parent_of_the_cache_refuses(self):
        # The revision-2 review's R2-1: the cache and its public entry exist, but the directory above them cannot be
        # searched, so the cache looked absent and the push went out. For theorem-labs/rocq, one of the sweeper's six,
        # the gate needs no cache: it guards, and the check, which cannot read the visibility either, refuses as git's
        # installed hook does for the same push. A public repository outside the six needs the cache, so the gate
        # refuses unread, saying the commits are not the problem.
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
        self.assert_refused_unread(r, c, f"cannot reach the public leak check's visibility cache {self.cache}")
        # A cache that is not there at all is no answer either way: the checkout is not known public, as on a machine
        # without the cache (a known gap of the sweeper's rule too). The same for a symlink to nothing.
        self.cache.rename(self.tmp / "cache-away")
        try:
            r = self.jj("--quiet", "git", "push", "-b", "b1")
            self.cache.symlink_to(self.tmp / "nowhere")
            r2 = self.jj("--quiet", "git", "push", "-b", "b1")
        finally:
            self.cache.unlink(missing_ok=True)
            (self.tmp / "cache-away").rename(self.cache)
        self.assert_pushed(r, c, url)
        self.assertEqual(r2.returncode, 0, r2.stderr)

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
        # that cannot be searched. The entry exists and says public, but [[ -e ]] read the cache as absent (and [[ -f ]]
        # skipped the entry), so jj sent the leak while git's installed hook refused it. Now only the kernel's ENOENT
        # means absent; this failure refuses unread.
        self.install_git_hook()
        url = "https://github.com/theorem-labs/cached-public.git"
        self.git("remote", "set-url", "origin", url)
        c = self.leak()
        entry = self.cache / "theorem-labs_cached-public"
        for path, why in ((self.home / ".cache", f"cannot reach the public leak check's visibility cache {self.cache}"),
                          (self.cache, f"cannot reach the public leak check's visibility cache {self.cache}"),
                          (entry, f"the public leak check's visibility cache entry {entry} is empty, unreadable, not a regular file")):
            g, r = self.hidden_push(path, commit=c)
            self.assertNotEqual(g.returncode, 0, g.stderr)
            self.assertIn("LEAK CHECK: cannot read the visibility of theorem-labs/cached-public", g.stderr)
            self.assert_refused_unread(r, c, why)
        # The review's content control: the `.cache` variant with only git@corp.example:theorem-labs/cached-public.git,
        # over git's ssh transport, a target the check scans without a visibility: git's hook refuses the content.
        self.use_ssh()
        url = "git@corp.example:theorem-labs/cached-public.git"
        self.git("remote", "set-url", "origin", url)
        self.git("config", "--unset-all", "remote.origin.vcs")
        sink = self.ssh_sink("git@corp.example", "theorem-labs/cached-public.git")
        g, r = self.hidden_push(self.home / ".cache", commit=c)
        self.assertNotEqual(g.returncode, 0, g.stderr)
        self.assertIn("LEAK CHECK: refusing to push", g.stderr)
        self.assert_refused_unread(r, c, f"cannot reach the public leak check's visibility cache {self.cache}")
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
        # The revision-2 review's R2-2: `git remote -v` keeps a space inside a URL, and the gate read the URL only up to
        # it, so these checkouts went unguarded while git's installed hook refused. A real local bare repository, then
        # the same path over ssh (the local-only adapter), each the checkout's only remote.
        self.install_git_hook()
        self.use_ssh()
        local = self.tmp / "mirror space" / "theorem-labs" / "rocq.git"
        subprocess.run(["/usr/bin/git", "init", "-q", "--bare", str(local)], check=True)
        self.assert_same_refusal_over_git_transport(str(local), local)
        path = "mirror space/theorem-labs/rocq.git"
        self.assert_same_refusal_over_git_transport(f"git@corp.example:{path}", self.ssh_sink("git@corp.example", path))
        self.assertTrue(self.ssh_sink("git@corp.example", path).is_dir(), "git did not reach the ssh adapter")

    def test_an_ssh_alias_with_a_hash_or_question_mark_is_guarded(self):
        # The revision-2 review's R2-5: # and ? are ordinary characters of an ssh host alias, and the gate cut the
        # alias there before it found the path. ssh resolves both aliases (checked below when ssh is here); git hands
        # the whole alias to ssh, here the local-only adapter.
        self.install_git_hook()
        self.use_ssh()
        cfg = self.tmp / "sshconfig"
        cfg.write_text('Host "alias#xf" "alias?xf"\n  HostName github.com\n', encoding="utf-8")
        for alias in ("alias#xf", "alias?xf"):
            ssh = shutil.which("ssh")
            if ssh:
                resolved = subprocess.run([ssh, "-F", str(cfg), "-G", f"git@{alias}"], capture_output=True, text=True)
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
        # The gate knows the checkout (its match ignores case, as the sweeper's does, and it needs no cache for the
        # sweeper's six); the check, which reads the cache lower-cased, finds no entry for theorem-labs/rocq, asks the
        # fake gh, gets nothing, and refuses for that reason alone, naming the repository as the URL writes it.
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


if __name__ == "__main__":
    unittest.main()
