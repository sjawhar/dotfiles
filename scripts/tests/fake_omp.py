"""One fake omp for the tests of the marketplace step of scripts/dotfiles-follow, of its call from
installers/omp.sh and of its call from scripts/dotfiles-sync. write() puts it at a path as an
executable Python script.

It models what Oh My Pi 18.18.6 (the pin of mise.toml) does, as INFRA-5366's plan measured it:

- `plugin marketplace list` prints the text of the pin: "No marketplaces configured", or
  "Configured Marketplaces:" and one `  <name>  <source>` line for each registration, in the order
  the registrations were added (test_dotfiles_follow_marketplace holds it equal, byte for byte, to
  the fixtures in omp-marketplace-list/).
- `plugin marketplace update <name>` exits 1 for an unknown name, for a name the state marks as
  failing, and when the catalog of the name's source is now named otherwise (Oh My Pi refuses an
  update across a change of the catalog's name).
- `plugin marketplace add <source>` registers the catalog under its current name, beside any other
  name, and exits 1 when the add is marked as failing or that name is registered already.
- `plugin marketplace remove <name>` removes the registration and keeps the plugins.
- `plugin install <plugin>@<catalog>` exits 1 for an unregistered catalog, for a plugin of the same
  name installed under another ID, and for a marked failure. It installs nothing when the lock
  holds the plugin's name, and exits 0 there by default (see the comment in the script).
  Otherwise it installs the catalog's version and writes the lock entry.
- `plugin uninstall <id>` removes the ID and its lock entry, and exits 1 for an ID that is not
  installed or a marked failure (which leaves it installed).
- `plugin upgrade <id>` sets the catalog's version and writes the lock entry.
- Any other command exits 99, so a command the code should not run fails its test.

The plugins are $DOTFILES_DIR/omp/plugins/installed_plugins.json and the lock is
$DOTFILES_DIR/omp/plugins/omp-plugins.lock.json: dotfiles-follow's run_step sets DOTFILES_DIR to the
checkout for every command.

Its state is the JSON file FAKE_OMP_STATE (none: no registration and no catalog):

    registrations     [[name, source], ...], in the order they were added
    catalogs          {source: {"name": the catalog's current name,
                                "versions": {plugin: version}}}; a source with no entry keeps the
                      name it is registered under
    version           a plugin's version where its catalog names none (default "0.2.0")
    fail              {"update": [names], "add": true, "remove": [names],
                       "install": [IDs], "install_noop": [IDs] (exit 0 and install nothing),
                       "uninstall": [IDs]}
    lock_refusal_exit the status of the install that the lock refuses (default 0; the pin's is 1)
    list_output       the text `list` prints in place of the registrations
    sleep             {"args": [...], "seconds": n, "marker": path}: the call with exactly these
                      arguments writes {"pid": its pid} to the marker, then sleeps

Its call log is FAKE_OMP_LOG: one JSON object a line, with the call's `args`, `has_omp_profile`
and `has_pi_profile` (whether OMP_PROFILE and PI_PROFILE were set), `agent_dir` (the value of
PI_CODING_AGENT_DIR, or null) and `path`, the path it ran as. With RECORD set it also appends
`omp <args>` to that file, so a test of the sync can order its calls after Bun's and mise's.

Under OMP_PROFILE or PI_PROFILE `legion` it logs the call and exits 1, as the fake of
test_dotfiles_follow_jj did. The environment knobs of the fake that test_dotfiles_follow_marketplace
held before this one keep their names and behaviour: FAKE_OMP_UPDATE_FAIL, FAKE_OMP_DELAY,
FAKE_OMP_FAIL, FAKE_OMP_HANG (with FAKE_OMP_MARKER), FAKE_OMP_INVALID_BYTES, FAKE_OMP_VERSIONS and
FAKE_OMP_DATA_LOG. FAKE_OMP_REWRITE_LOCK=1 makes every call write {"plugins": {}, "rewritten": true}
to the lock, as that fake always did; it is off by default, since a lock emptied on every call
hides the install that the lock refuses.
"""

from __future__ import annotations

import json
from pathlib import Path

# The names the tests use: the team catalog's source and its name before and after the rename of
# dispatch://INFRA-4807, and a catalog of another repository.
TEAM_SOURCE = "theorem-labs/verification-skills"
OLD_NAME = "agent-skills"
NEW_NAME = "theorem-skills"
OTHER_NAME = "other-cat"
OTHER_SOURCE = "example/other"
TEAM_PLUGINS = ("verification", "tasks", "engineering", "dev-infra")

SCRIPT = r'''#!/usr/bin/env python3
# The fake omp of scripts/tests/fake_omp.py, whose docstring says what it models.
import json
import os
import subprocess
import sys
import time
from pathlib import Path

args = sys.argv[1:]
env = os.environ
with Path(env["FAKE_OMP_LOG"]).open("a") as log:
    log.write(json.dumps({
        "args": args,
        "has_omp_profile": "OMP_PROFILE" in env,
        "has_pi_profile": "PI_PROFILE" in env,
        "agent_dir": env.get("PI_CODING_AGENT_DIR"),
        "path": os.path.abspath(sys.argv[0]),
    }) + "\n")
if env.get("RECORD"):
    with Path(env["RECORD"]).open("a") as log:
        log.write("omp " + " ".join(args) + "\n")
if env.get("FAKE_OMP_DATA_LOG"):
    with Path(env["FAKE_OMP_DATA_LOG"]).open("a") as log:
        log.write(env.get("MISE_DATA_DIR", "unset") + "\n")
if env.get("OMP_PROFILE") == "legion" or env.get("PI_PROFILE") == "legion":
    sys.exit(1)

state_path = Path(env["FAKE_OMP_STATE"]) if env.get("FAKE_OMP_STATE") else None
state = json.loads(state_path.read_text()) if state_path and state_path.exists() else {}
registrations = state.get("registrations", [])
fail = state.get("fail", {})


def plugins_dir():
    return Path(env["DOTFILES_DIR"]) / "omp/plugins"


def lock_path():
    return plugins_dir() / "omp-plugins.lock.json"


if env.get("FAKE_OMP_REWRITE_LOCK") == "1":
    lock_path().write_text('{"plugins": {}, "rewritten": true}\n')

sleep = state.get("sleep")
if sleep and sleep.get("args") == args:
    marker = Path(sleep["marker"])
    marker.with_name(marker.name + ".new").write_text(json.dumps({"pid": os.getpid()}))
    os.replace(marker.with_name(marker.name + ".new"), marker)
    time.sleep(float(sleep["seconds"]))


def refuse(message, status=1):
    print("\u2718 " + message, file=sys.stderr)
    sys.exit(status)


def bare(source):
    """SOURCE as owner/repository, without case, a GitHub prefix or a final .git or /."""
    text = source.lower()
    for prefix in ("https://github.com/", "git@github.com:", "ssh://git@github.com/"):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    if text.endswith("/"):
        text = text[:-1]
    elif text.endswith(".git"):
        text = text[:-4]
    return text


def catalog(source):
    for key, value in state.get("catalogs", {}).items():
        if bare(key) == bare(source):
            return value
    return None


def registered(name):
    return next((source for known, source in registrations if known == name), None)


def catalog_name(source, name):
    found = catalog(source)
    return found["name"] if found and "name" in found else name


def catalog_version(source, plugin):
    found = catalog(source) or {}
    return found.get("versions", {}).get(plugin, state.get("version", "0.2.0"))


def save_state():
    state["registrations"] = registrations
    state_path.write_text(json.dumps(state))


def read_json(path, empty):
    return json.loads(path.read_text()) if path.exists() else empty


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n")


if args == ["plugin", "marketplace", "list"]:
    if "list_output" in state:
        sys.stdout.write(state["list_output"])
    elif not registrations:
        sys.stdout.write("No marketplaces configured\n\nAdd one with: omp plugin marketplace add <source>\n")
    else:
        sys.stdout.write("Configured Marketplaces:\n\n" + "".join(f"  {name}  {source}\n" for name, source in registrations))
    sys.exit(0)

if args[:3] == ["plugin", "marketplace", "update"] and len(args) == 4:
    time.sleep(float(env.get("FAKE_OMP_DELAY") or 0))
    name = args[3]
    if name == env.get("FAKE_OMP_UPDATE_FAIL"):
        print("catalog refresh failed", file=sys.stderr)
        sys.exit(8)
    source = registered(name)
    if source is None:
        refuse(f'Marketplace "{name}" not found')
    if name in fail.get("update", []):
        refuse(f"Failed to update marketplace: Error: git fetch of {source} failed: Could not resolve host: github.com")
    current = catalog_name(source, name)
    if current != name:
        refuse(
            f'Failed to update marketplace: Error: Marketplace catalog name changed from "{name}" to "{current}".'
            " Remove and re-add the marketplace to update."
        )
    print(f"\u2714 Updated marketplace {name}")
    sys.exit(0)

if args[:3] == ["plugin", "marketplace", "add"] and len(args) == 4:
    source = args[3]
    if fail.get("add"):
        refuse(f"Failed to add marketplace: Error: git clone of {source} failed: Could not resolve host: github.com")
    found = catalog(source)
    if found is None:
        refuse(f"Failed to add marketplace: Error: no catalog at {source}")
    name = found["name"]
    if registered(name) is not None:
        refuse(f'Failed to add marketplace: Marketplace "{name}" already exists')
    registrations.append([name, source])
    save_state()
    print(f"\u2714 Added marketplace {name}")
    sys.exit(0)

if args[:3] == ["plugin", "marketplace", "remove"] and len(args) == 4:
    name = args[3]
    if registered(name) is None or name in fail.get("remove", []):
        refuse(f'Failed to remove marketplace "{name}"')
    registrations = [entry for entry in registrations if entry[0] != name]
    save_state()
    print(f"\u2714 Removed marketplace {name}")
    sys.exit(0)

if args[:2] in (["plugin", "install"], ["plugin", "uninstall"]) and len(args) == 3:
    ident = args[2]
    plugin, _, name = ident.rpartition("@")
    installed_path = plugins_dir() / "installed_plugins.json"
    installed = read_json(installed_path, {"version": 2, "plugins": {}})
    plugins = installed.setdefault("plugins", {})
    lock = read_json(lock_path(), {"plugins": {}})
    if args[1] == "uninstall":
        if ident not in plugins:
            refuse(f'Plugin "{ident}" is not installed')
        if ident in fail.get("uninstall", []):
            refuse(f"Failed to uninstall {ident}: EBUSY: resource busy or locked")
        del plugins[ident]
        write_json(installed_path, installed)
        lock.setdefault("plugins", {}).pop(plugin, None)
        write_json(lock_path(), lock)
        print(f"\u2714 Uninstalled {ident}")
        sys.exit(0)
    source = registered(name)
    if source is None:
        refuse(f'Marketplace "{name}" not found')
    holder = next((other for other in plugins if other.rpartition("@")[0] == plugin and other != ident), None)
    if holder is not None:
        refuse(f'Runtime package name "{plugin}" conflicts with installed plugin "{holder}"')
    if ident in fail.get("install", []):
        refuse(f"Failed to install {ident}: Error: Could not resolve host: github.com")
    if plugin in lock.get("plugins", {}):
        # The pin refuses this install with exit 1. The fake exits 0 by default on purpose: that is
        # the harder case, since the code must judge each install by installed_plugins.json
        # whatever the status. lock_refusal_exit 1 gives the pin's status.
        refuse(f'Runtime package name "{plugin}" conflicts with installed package "{plugin}"', state.get("lock_refusal_exit", 0))
    if ident in fail.get("install_noop", []):
        sys.exit(0)
    version = catalog_version(source, plugin)
    plugins[ident] = [{"scope": "user", "version": version}]
    write_json(installed_path, installed)
    lock.setdefault("plugins", {})[plugin] = {"version": version, "enabledFeatures": None, "enabled": True}
    write_json(lock_path(), lock)
    print(f"\u2714 Installed {ident}")
    sys.exit(0)

if args[:2] == ["plugin", "upgrade"] and len(args) == 3:
    ident = args[2]
    plugin, _, name = ident.rpartition("@")
    installed_path = plugins_dir() / "installed_plugins.json"
    installed = json.loads(installed_path.read_text())
    record = installed["plugins"][ident][0]
    if plugin in env.get("FAKE_OMP_FAIL", "").split(","):
        record["version"] = "partial"
        installed_path.write_text(json.dumps(installed))
        print("upgrade failed midway: " + plugin, file=sys.stderr)
        sys.exit(7)
    if plugin == env.get("FAKE_OMP_HANG"):
        record["version"] = "partial"
        installed_path.write_text(json.dumps(installed))
        marker = Path(env["FAKE_OMP_MARKER"])
        late = marker.with_suffix(".late")
        child = subprocess.Popen([
            sys.executable, "-c",
            "import pathlib, time; time.sleep(2); "
            + "pathlib.Path(" + repr(str(lock_path())) + ").write_text('late child write'); "
            + "pathlib.Path(" + repr(str(late)) + ").write_text('child survived')",
        ], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        marker.write_text(json.dumps({"pid": child.pid, "deadline": time.monotonic() + 2}))
        time.sleep(30)
    versions = json.loads(env["FAKE_OMP_VERSIONS"]) if env.get("FAKE_OMP_VERSIONS") else {}
    if plugin in versions:
        version = versions[plugin]
    else:
        source = registered(name)
        if source is None:
            refuse(f'Marketplace "{name}" not found')
        version = catalog_version(source, plugin)
    record["version"] = version
    installed_path.write_text(json.dumps(installed))
    lock = read_json(lock_path(), {"plugins": {}})
    lock.setdefault("plugins", {}).setdefault(plugin, {})["version"] = version
    write_json(lock_path(), lock)
    if plugin == env.get("FAKE_OMP_INVALID_BYTES"):
        os.write(1, b"\xff\n")
        sys.exit(7)
    sys.exit(0)

print("unexpected omp arguments: " + repr(args), file=sys.stderr)
sys.exit(99)
'''


def write(path: Path) -> Path:
    """Write the fake to PATH, executable, and return PATH."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(SCRIPT)
    path.chmod(0o755)
    return path


def write_state(path: Path, state: dict) -> None:
    path.write_text(json.dumps(state))


def read_state(path: Path) -> dict:
    return json.loads(path.read_text())


def read_calls(log: Path) -> list[dict]:
    """The fake's call log: one dict a call, oldest first ([] before the first call)."""
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
