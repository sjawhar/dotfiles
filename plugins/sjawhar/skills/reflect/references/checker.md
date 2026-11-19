# Checker brief

The lead pastes this file whole into the checker's task, with the records directory and the reader output files. You are a fresh agent, not the lead and not a reader: you hold the list of records and decide what was read.

1. Run the mechanical check, then read `<records dir>/check.json` for the detail:

   ```bash
   python3 ~/.dotfiles/plugins/sjawhar/skills/reflect/scripts/check-findings.py <records dir> <rows files...>
   ```

   A missing source is a stale path, not a bad quote. A quote the record does not hold is a mis-copy or a fabrication, and only reading decides which.
2. Open every flagged finding in its record. A quote a reader shortened or re-spaced that says the same thing: fix the quote in the row to the exact text, and fix `instruction_source` to a path that exists. A quote the record does not hold: reject the finding. Sami's words that reach the record only through an agent's relay are not his words: reject the pattern or pushback that rests on them.
3. Open 10 more findings at random, so a plausible invented row is caught even when its quotes exist, and every record whose row has no findings but whose Sami message objects to something ("No", "Why", "I said", "Stop", a swear). Reject a finding when:
   - the agent did right, or Sami accepted the choice;
   - the missing action could have happened outside the record;
   - it depends on facts the record does not show;
   - the row's quotes do not show the same failure (the action does not break the quoted instruction, or the pushback is about something else).

   For a record with an objection and no finding, say what the reader missed.
4. Write `verdict.json` in the records directory: `{"read": n, "not_read": [ids], "rejected": [{"id": "M0012#0", "reason": "..."}], "missed": [{"record": "M0040", "what": "..."}], "quotes_fixed": n}`.

Your final message: the records read out of the total, the batches to run again (their records have no row), the findings rejected and the rejection rate, and the records where a failure was missed.
