"""pytest plugin, loaded in every agent box: says loudly when sandbox tests were skipped.

A test that needs bubblewrap or an unprivileged user namespace cannot run in an agent box: Sysbox
(the box runtime) handles every procfs mount itself and refuses one made from a nested user
namespace, so a bwrap run that mounts its own /proc fails, and the image ships no bwrap. Such tests skip ("bwrap
not installed") and pytest still ends green. This plugin prints a banner after the summary that
names how many were skipped and why, so the run is not read as proof that the sandbox works.
"""

import os
import re

SANDBOX_REASON = re.compile(r"bwrap|bubblewrap|user[ _-]?namespace|userns|unshare|sandbox", re.IGNORECASE)


def _reason(report):
    longrepr = report.longrepr
    if isinstance(longrepr, tuple) and len(longrepr) == 3:
        reason = str(longrepr[2])
        return reason[len("Skipped: "):] if reason.startswith("Skipped: ") else reason
    return str(longrepr)


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    if not os.path.exists("/etc/agentbox-identity"):
        return
    reasons = {}
    for report in terminalreporter.stats.get("skipped", []):
        reason = _reason(report)
        if SANDBOX_REASON.search(reason):
            reasons[reason] = reasons.get(reason, 0) + 1
    if not reasons:
        return
    total = sum(reasons.values())
    config._agentbox_sandbox_skips = total
    tr = terminalreporter
    tr.write_sep("!", f"AGENT BOX: {total} SANDBOX TEST(S) SKIPPED, NOT VERIFIED", red=True, bold=True)
    for reason, count in sorted(reasons.items(), key=lambda item: -item[1]):
        tr.write_line(f"  {count} x {reason}", red=True, bold=True)
    tr.write_line(
        "  An agent box cannot run bubblewrap: Sysbox refuses a /proc mount from a nested user namespace."
        " These tests did not run, so this result says nothing about the sandbox. Run them on the host"
        " or in CI before you report them green (agentbox/AGENTS.md).",
        red=True,
        bold=True,
    )


def pytest_unconfigure(config):
    # Once more after pytest's own "N passed, M skipped" line, for a reader who keeps only the tail.
    total = getattr(config, "_agentbox_sandbox_skips", 0)
    tr = config.pluginmanager.get_plugin("terminalreporter")
    if total and tr is not None:
        tr.write_line(f"AGENT BOX: {total} sandbox test(s) skipped, not verified (see the banner above)",
                      red=True, bold=True)
