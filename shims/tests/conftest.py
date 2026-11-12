"""Give every shim test the encrypted test term list."""
import os

from leak_terms_fixture import fixture_environment


def pytest_configure() -> None:
    os.environ.update(fixture_environment())
