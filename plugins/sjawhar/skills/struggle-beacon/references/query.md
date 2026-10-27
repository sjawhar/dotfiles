# Find beacons

Every load of skill://struggle-beacon with session, time and the next assistant line:

```bash
tq sql "select t.session_id, t.started_at, substring(argMinIf(m.text, m.ts, m.ts>t.started_at and m.text!=''),1,300) as next_line from calls t left join messages m on m.session_id=t.session_id and m.role='assistant' and m.kind='assistant' where t.tool='read' and position(t.command,'skill://struggle-beacon')>0 group by t.session_id, t.started_at order by t.started_at"
```
