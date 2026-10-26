# Decision rights

| The step | Who decides |
| --- | --- |
| Easy to undo: a code change, a flag, a branch, opening or merging a pull request that passed peer review and tests, a planned experiment, and any spending the workflow itself calls for (a paid-API review, compute, a VM, a model run the task needs) | The agent. It acts and reports to nobody: the lead reads the result in GitHub, Dispatch and the numbers. |
| Hard to undo: deleting data someone else created, a new financial commitment (a contract, a subscription), a message outside the organization, security, another stream's work or shared state | The agent takes it to its owner directly: the other stream's agent or lead, the owner of the shared infrastructure. A decision for Sami goes to the lead first, and to his Dispatch Inbox only when the lead cannot answer it ([questions-for-sami.md](questions-for-sami.md)). |
