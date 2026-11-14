"""Give every shim test the encrypted test term list."""
import os
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from leak_terms_fixture import fixture_environment
from test_person_branch_guard import PersonBranchGuardTest


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


# test_person_branch_guard builds each command's environment from nothing, so neither the list above nor sops reaches
# its commits through the shims. Its env() gets the list's variables and, last on PATH, a directory holding only sops.
# The class is imported by name so a rename fails here at once; subclasses inherit the wrapped env(). The file itself
# is #53's and stays as main has it.
_person_branch_guard_env = PersonBranchGuardTest.env


def _fixture_person_branch_guard_env(self, *args, **kwargs):
    result = {**_person_branch_guard_env(self, *args, **kwargs), **fixture_environment()}
    result["PATH"] = os.pathsep.join([result["PATH"], _sops_directory()])
    return result


PersonBranchGuardTest.env = _fixture_person_branch_guard_env


def pytest_collection_modifyitems(items) -> None:
    for item in items:
        if item.module.__name__ == "test_person_branch_guard":
            assert item.module.PersonBranchGuardTest is PersonBranchGuardTest
