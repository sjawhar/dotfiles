---
alwaysApply: true
---
# Dispatch defects and usability requests go to INFRA

Dispatch's own code lives in the INFRA project. When a `dispatch` command is hard to use, or stores, changes or loses something wrongly, open an INFRA issue with a repro (correctness first) and tell `notifications.role.legion-infra-controller`, rather than working around it. This is the Theorem resolution of the generic rule in `skill://dispatch`; a Legion role routes the finding to its architect or controller instead (`skill://legion-worker/references/findings.md`).
