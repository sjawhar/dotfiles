"""Give every shim test the encrypted test term list."""
import os
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

import pytest

from leak_terms_fixture import fixture_environment


def pytest_configure() -> None:
    os.environ.update(fixture_environment())


@lru_cache(maxsize=1)
def _sops_directory() -> str:
    """A directory holding only `sops`, linked to the binary this run's sops resolves to (past a mise shim, which reads
    the caller's home)."""
    found = shutil.which("sops")
    if found is None:
        raise RuntimeError("sops is not on PATH")
    mise = shutil.which("mise")
    if mise is not None:
        resolved = subprocess.run([mise, "which", "sops"], capture_output=True, text=True, check=False)
        if resolved.returncode == 0 and resolved.stdout.strip():
            found = resolved.stdout.strip()
    directory = Path(fixture_environment()["DOTFILES_LEAK_TERMS_FILE"]).parent / "sops-bin"
    directory.mkdir(exist_ok=True)
    link = directory / "sops"
    if not link.exists():
        link.symlink_to(found)
    return str(directory)


@pytest.fixture(autouse=True)
def _leak_terms_in_environments_built_from_nothing(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    # test_person_branch_guard builds each command's environment from nothing, so neither the list above nor sops
    # reaches its commits through the shims. Its env() gets the list's three variables and, last on PATH, a directory
    # holding only sops.
    cls = request.cls
    if cls is None or cls.__name__ != "PersonBranchGuardTest":
        return
    built = cls.env

    def env(self, *args, **kwargs):
        result = {**built(self, *args, **kwargs), **fixture_environment()}
        result["PATH"] = os.pathsep.join([result["PATH"], _sops_directory()])
        return result

    monkeypatch.setattr(cls, "env", env)
