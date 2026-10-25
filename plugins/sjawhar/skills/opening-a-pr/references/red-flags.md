# Red flags: go run step 4

Each excuse below is a reason agents give for skipping the end-to-end proof. When you catch yourself giving one, run step 4.

| Excuse | Reality |
|---|---|
| "CI is green." "The mechanism says it works." "Verified by inspection." | You saw a mechanism, not a result. |
| "Only a pin, a prompt, a doc." "Too small to boot." | What runs changed. Find its consumer and run it. |
| "I verified it before the last fixes." | Those fixes are the diff now. |
| "I disclosed the gap." "No test environment here." | Report NOT merge-ready, do not open it for review, and build the missing surface. |
| "It gets verified after the merge." "Sami wanted it fast." "Separate PR." "Follow-up issue?" | That is deferral: it is unverified now. Do it now, and never open an issue Sami did not ask for. |
| "Want me to run the smoke test?" | Do not ask. Running it is the job. |
| "Nothing is actionable until you merge." | Read this list again. It is usually false. |
