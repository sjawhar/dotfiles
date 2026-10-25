# Checker brief

The lead pastes this file whole into the checker's task, with the records directory and the reader output files. The checker is a fresh agent: not the lead, not a reader. The readers cannot close the run; you hold the list of records and decide what was read.

1. Run the mechanical check:

   ```bash
   python3 ~/.dotfiles/plugins/sjawhar/skills/reflect/scripts/check-findings.py <records dir> <rows files...>
   ```

   It lists records with no row (and their batches), duplicate rows, quotes not found in their record, and pushbacks that are not in Sami's words. Read `<records dir>/check.json` for the detail.
2. Open every flagged finding in its record. A quote that a reader shortened or re-spaced but that says the same thing: fix the quote in the row to the exact text. A quote that is not there: reject the finding.
3. Open 10 more findings at random, so a reader that invented a plausible row is caught even when its quotes exist, and every record whose row has no findings but whose Sami message objects to something ("No", "Why", "I said", "Stop", a swear). Judge each against these reject types:
   - the agent did right, or Sami accepted the choice;
   - the missing action could have happened outside the record;
   - it depends on facts the record does not show;
   - the row's quotes do not show the same failure (the action does not break the quoted instruction, or the pushback is about something else).
   A record with an objection and no finding: say what the reader missed.
4. Write `verdict.json` in the records directory: `{"read": n, "not_read": [ids], "rejected": [{"id": "M0012#0", "reason": "..."}], "missed": [{"record": "M0040", "what": "..."}], "quotes_fixed": n}`.

Your final message: the records read out of the total, the batches to run again (their records have no row), the findings rejected and the rejection rate, and the records where a failure was missed. The lead runs the listed batches again with fresh readers and calls you again, at most three rounds.
