"""An isolated encrypted term list for shim and script tests."""
from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path


TEST_TERMS = ("Quillfeather Lane 9", "zorbleplex", "vintrapolis-canary-7")
# A term listed for one set of repositories only (the list's `scoped` map): a push or gh text bound for a public
# repository the pattern matches refuses it, and one bound for any other repository does not.
SCOPED_TEST_TERMS = {"cybermonklabs/*": ("brindlewort-scoped-5",)}
# The forbidden list of knives's fork registry entry oh-my-pi: a push or a gh text bound for a public repository named
# oh-my-pi, whatever its owner, and a `diff` or `message` given --repository OWNER/oh-my-pi refuse them; a text bound
# for any other repository does not.
REGISTRY_TEST_TERMS = {"oh-my-pi": ("knurlwick-registry-1", "plumbago-registry-2")}
# Tokens that hold the scoped term and are allowed in one repository (the list's `allowed` map): a third-party npm
# package as written and as a JavaScript regular expression escapes its slash.
ALLOWED_TEST_TOKENS = {
    "cybermonklabs/oh-my-pi": ("@brindlewort-scoped-5/mcp-server-browser", "@brindlewort-scoped-5\\/mcp-server-browser"),
}


def _run_age_keygen(destination: Path) -> None:
    completed = subprocess.run(["age-keygen", "-o", str(destination)], capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError("age-keygen could not create the test key")


def _recipient(key_file: Path) -> str:
    for line in key_file.read_text(encoding="utf-8").splitlines():
        if line.startswith("# public key: "):
            return line.removeprefix("# public key: ")
    raise RuntimeError("age-keygen did not write a recipient")

def _encrypt_yaml(yaml_text: str, recipient: str) -> bytes:
    completed = subprocess.run(
        [
            "sops",
            "encrypt",
            "--filename-override",
            "leak-terms.sops.yaml",
            "--input-type",
            "yaml",
            "--output-type",
            "yaml",
            "--age",
            recipient,
            "/dev/stdin",
        ],
        input=yaml_text.encode(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("sops could not encrypt the test term list")
    return completed.stdout


def encrypt_yaml(yaml_text: str, output: Path | None = None) -> Path:
    """Encrypt YAML with the process-wide test key, passing plaintext only on stdin."""
    fixture = fixture_environment()
    root = Path(fixture["DOTFILES_LEAK_TERMS_FILE"]).parent
    if output is None:
        descriptor, name = tempfile.mkstemp(prefix="terms-", suffix=".sops.yaml", dir=root)
        os.close(descriptor)
        output = Path(name)
    output.write_bytes(_encrypt_yaml(yaml_text, _recipient(Path(fixture["SOPS_AGE_KEY_FILE"]))))
    return output


def unrelated_key_file() -> Path:
    fixture = fixture_environment()
    root = Path(fixture["DOTFILES_LEAK_TERMS_FILE"]).parent
    descriptor, name = tempfile.mkstemp(prefix="unrelated-age-", dir=root)
    os.close(descriptor)
    key = Path(name)
    key.unlink()
    _run_age_keygen(key)
    return key


@lru_cache(maxsize=1)
def real_sops() -> str:
    """The binary this run's `sops` resolves to, past a mise shim, which reads the caller's home."""
    found = shutil.which("sops")
    if found is None:
        raise RuntimeError("sops is not on PATH")
    mise = shutil.which("mise")
    if mise is not None:
        resolved = subprocess.run([mise, "which", "sops"], capture_output=True, text=True, check=False)
        if resolved.returncode == 0 and resolved.stdout.strip():
            found = resolved.stdout.strip()
    return found


@lru_cache(maxsize=1)
def sops_directory() -> str:
    """A directory holding only `sops`, linked to the binary this run's sops resolves to (past a mise shim, which reads
    the caller's home)."""
    directory = Path(fixture_environment()["DOTFILES_LEAK_TERMS_FILE"]).parent / "sops-bin"
    directory.mkdir(exist_ok=True)
    link = directory / "sops"
    if not link.exists():
        link.symlink_to(real_sops())
    return str(directory)


def leak_check_environment(path: str) -> dict[str, str]:
    """For an environment a test builds from nothing, with PATH PATH: fixture_environment()'s four variables (the
    list, its key, sops's home and the knives registry), and PATH with sops_directory() last, which every gh write,
    commit and push through the shims needs to read the list."""
    return {**fixture_environment(), "PATH": os.pathsep.join([path, sops_directory()])}


@lru_cache(maxsize=1)
def fixture_environment() -> dict[str, str]:
    """Build the encrypted list, and a knives registry whose oh-my-pi entry forbids REGISTRY_TEST_TERMS (one in
    repos.toml, one in the private repos.local.toml), once for this Python process, and clean them at exit."""
    root = Path(tempfile.mkdtemp(prefix="leak-terms-"))
    atexit.register(shutil.rmtree, root, ignore_errors=True)
    key_file = root / "age-key.txt"
    _run_age_keygen(key_file)
    sops_home = root / "sops-home"
    sops_home.mkdir()

    plaintext = "terms: |\n  # test comment\n" + "".join(f"  {term}\n" for term in TEST_TERMS)
    plaintext += "scoped:\n" + "".join(
        f'  "{pattern}": |\n' + "".join(f"    {term}\n" for term in terms) for pattern, terms in SCOPED_TEST_TERMS.items()
    )
    plaintext += "allowed:\n" + "".join(
        f'  "{pattern}": |\n    # a third-party npm package that holds the scoped term\n'
        + "".join(f"    {token}\n" for token in tokens)
        for pattern, tokens in ALLOWED_TEST_TOKENS.items()
    )
    term_file = root / "leak-terms.sops.yaml"
    term_file.write_bytes(_encrypt_yaml(plaintext, _recipient(key_file)))
    knives = root / "knives"
    knives.mkdir()
    public, private = REGISTRY_TEST_TERMS["oh-my-pi"]
    (knives / "repos.toml").write_text(
        '[repos.oh-my-pi]\nupstream = "https://github.com/upstream-fixture/oh-my-pi"\n'
        f'origin = "https://github.com/sjawhar/oh-my-pi"\nforbidden = ["{public}"]\n\n'
        '[repos.unlisted]\nupstream = "https://github.com/upstream-fixture/unlisted"\n',
        encoding="utf-8",
    )
    (knives / "repos.local.toml").write_text(f'[repos.oh-my-pi]\nforbidden = ["{private}"]\n', encoding="utf-8")
    return {
        "DOTFILES_LEAK_TERMS_FILE": str(term_file),
        "SOPS_AGE_KEY_FILE": str(key_file),
        "DOTFILES_LEAK_TERMS_SOPS_HOME": str(sops_home),
        "DOTFILES_LEAK_TERMS_KNIVES_DIR": str(knives),
    }
