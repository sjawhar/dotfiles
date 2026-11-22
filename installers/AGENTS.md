# installers

- A second run of an installer changes nothing.
- A step for one machine role goes in a role installer that the role's install script runs, never in the root `install.sh`: the role must be explicit.
- Before an installer changes a unit or system state, it checks that state. A unit that is disabled and inactive was stopped on purpose: the installer leaves it alone and says so.
- Installer tests are in `scripts/tests/`, each named for its installer or helper (`test_docker_installer.py`, `test_ensure_link.py`); run one from the repository root: `PYTHONPATH=. uvx pytest scripts/tests/<file>`.

Where this file and the code disagree, the code is right: follow the code and correct this file.
