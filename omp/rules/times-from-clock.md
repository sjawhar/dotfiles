---
alwaysApply: true
---
# Every time you write comes from a clock you read, never from your head

Before you write a clock time — in a record, a status line, a message, a commitment, a pull request — read it: `date -u`, the record's own timestamp field, or the event time Dispatch or GitHub reports. Never type the time you believe it is, and never round the time you read up to the next slot.

Two failures this prevents, both counted on this box on 2026-11-07, in sessions whose work was otherwise sound: a session wrote "05:50Z" at 05:46:59Z, and six sessions in one night each stamped a record with a time a few minutes ahead of when they wrote it. A record dated ahead of itself cannot be ordered against anything else, and a reader who later measures from it measures from a moment that never happened.

In a script, `now` is a clock call (`datetime.now(timezone.utc)`, `date -u`), never a string literal: one typed literal at the top of a script stamps every field it writes. A duration is the difference of two timestamps you read, never a figure added to someone else's.

A time that has not happened yet is a plan, so write it as one: say what it is due by or after, and where that comes from (say, "by 18:00Z, the owner's commitment on that issue"). A commitment whose time you cannot source is not ready to be written down.
