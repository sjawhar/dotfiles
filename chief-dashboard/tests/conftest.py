import shutil
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def socket_dir():
    """A directory for a test's unix socket. A socket's path holds at most 107 bytes, and tmp_path
    under an agent session's TMPDIR (/mnt/scratch/<user>/session-tmp/<pid>-<random>/pytest-of-<user>/
    …) runs past that, so bind() failed with "AF_UNIX path too long" there."""
    path = Path(tempfile.mkdtemp(prefix="chief-sock-", dir="/tmp"))
    yield path
    shutil.rmtree(path, ignore_errors=True)
