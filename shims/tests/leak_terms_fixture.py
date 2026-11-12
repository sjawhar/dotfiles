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
def fixture_environment() -> dict[str, str]:
    """Build the encrypted list once for this Python process and clean it at exit."""
    root = Path(tempfile.mkdtemp(prefix="leak-terms-"))
    atexit.register(shutil.rmtree, root, ignore_errors=True)
    key_file = root / "age-key.txt"
    _run_age_keygen(key_file)
    sops_home = root / "sops-home"
    sops_home.mkdir()

    plaintext = "terms: |\n  # test comment\n" + "".join(f"  {term}\n" for term in TEST_TERMS)
    term_file = root / "leak-terms.sops.yaml"
    term_file.write_bytes(_encrypt_yaml(plaintext, _recipient(key_file)))
    return {
        "DOTFILES_LEAK_TERMS_FILE": str(term_file),
        "SOPS_AGE_KEY_FILE": str(key_file),
        "DOTFILES_LEAK_TERMS_SOPS_HOME": str(sops_home),
    }
