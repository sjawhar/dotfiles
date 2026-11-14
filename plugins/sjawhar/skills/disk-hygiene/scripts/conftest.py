"""Give the disk-hygiene tests the encrypted test term list: they commit through the dotfiles shims."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[5] / "shims" / "tests"))

from leak_terms_fixture import fixture_environment


def pytest_configure() -> None:
    os.environ.update(fixture_environment())
