# Classifying a slice, and what proves it

| Class | Proven by |
|---|---|
| Platform or application development | the application's own surface |
| Infrastructure development | what the repo's own instructions name |
| Task authoring (evaluation tasks, other content the repo ships) | running the task |
| Task queueing (finished content through the repo's submission or QA queue) | the state the queue records per item |

- Take each class's commands, driver, acceptance and PR template from the repo: AGENTS.md, the contributing guide, its skills, the CI that gates those paths. This skill supplies none.
- Never prove one class with another class's checks. Work that crosses boundaries exercises every boundary it touches and the join between them.
- A repo template that asks for another class's checks: follow the class the change is, and name the mismatch in the plan.
- No documented workflow for a class: derive verification from the consumer-surface rule in `opening-a-pr`.
