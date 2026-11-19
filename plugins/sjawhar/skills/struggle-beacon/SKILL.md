---
name: struggle-beacon
description: "Use when you are really struggling or very confused: the same approach failed two or more times, you are unsure what is being asked, or you realize a path you took wasted effort. Also when you are about to try 'one more' variant of a fix that keeps failing."
---

# Struggle beacon

Loading this skill is the marker: reviewers find where agents struggled by its loads.

1. Write one line starting `STRUGGLE:` that says what is hard or what was wasted.
2. Carry on with the task as you would have. Do not stop or ask anyone because of this skill.

## Finding beacons

For reviewers: every load of this skill, with its session, time and the next assistant line.

```bash
tq sql "select t.session_id, t.started_at, substring(argMinIf(m.text, m.ts, m.ts>t.started_at and m.text!=''),1,300) as next_line from calls t left join messages m on m.session_id=t.session_id and m.role='assistant' and m.kind='assistant' where t.tool='read' and position(t.command,'skill://struggle-beacon')>0 group by t.session_id, t.started_at order by t.started_at"
```
