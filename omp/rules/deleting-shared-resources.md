---
description: Deleting a Kubernetes Job or pod, a VM, a container, a branch or a worktree you did not create
condition:
  - 'kubectl[^\n]*\bdelete\b'
  - 'docker[^\n]*\b(rm|kill|stop)\b'
  - 'gcloud[^\n]*\bdelete\b'
  - 'git[^\n]*(worktree\s+remove|branch\s+-D|push[^\n]*--delete|branch[^\n]*--delete)'
scope: "tool:bash, tool:eval"
interruptMode: never
---
# Don't delete what you did not create

Every session on the cluster and the dev boxes shares one identity, so a resource you delete looks like anyone's. A failed Job or pod at least leaves a result; a DELETED one writes nothing, and its owner finds out only by noticing it is gone.

Before you delete any Kubernetes Job or pod, VM, container, branch, worktree, or other resource you did not create:

- Read its owner and whether it is still working: the `owner` / `created-by` label, a container's `owner=` label, a branch's author, a Job's labels and age.
- If it is another owner's and still running, leave it. If you cannot tell whose it is or whether it is live, ask the owner before deleting.
- Clearing capacity is never a reason to delete another lane's running work. Free your own resources, or ask the owner or the SRE to free theirs.

Sami, 2026-11-02: "Stop touching other people's stuff."
