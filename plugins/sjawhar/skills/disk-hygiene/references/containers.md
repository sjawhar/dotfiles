# Container verdicts (`disk_hygiene.py containers`)

How the census sets each verdict. The removal rules are in SKILL.md, Phase 5 step 5.

| Family | Identified by | Verdict |
|---|---|---|
| platform-e2e | `trajectory.platform-e2e=1`, `.role`, `.run-id` | Created, never started, >1 h: `leaked`. Running with a run-id: `live` if a host process has `TRAJECTORY_PLATFORM_E2E_RUN_ID=<id>` in its environment, or if its postgres port has clients. Empty run-id (a local run): only clients decide, with docker's Created time as grace. Unlabelled containers of the same name shape: same rules, role `unlabelled`. |
| local-stack | `trajectory.local-stack{,.name,.owner}`, names `tl-platform-<name>-<owner>-db` | Owner is `sha256("<uid>:<checkout>/platform/tl_platform")[:16]`. `live` if a process is under the checkout or its port has clients. `idle` if the checkout exists with nothing running. `leaked` if the checkout is gone, or if it never started. |
| ryuk | `testcontainers-ryuk-<session>` | Created: `stranded`. Remove it with its `org.testcontainers.session-id` fixtures. A running ryuk removes its session about 10 s after its client socket drops. |
| tc-fixture | `org.testcontainers.session-id` | Takes its ryuk's verdict. With no ryuk: `orphan-no-ryuk`, because no live owner can exist. Reap after a 15-minute startup grace with zero clients. |
| agentbox | session infrastructure | `never` |

The client check matches `docker ps` visibility. Whoever sees the container is on the daemon that its port binds to.
