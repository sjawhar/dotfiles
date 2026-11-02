---
name: writing-a-standup-recap
description: "Use for a standup recap, including a short chat update and a stream lead's standup lines. Also for any weekly or daily summary of delivered work for the team, or 'what did we ship'. Not for agent-to-agent status."
---

# Writing a standup recap

The readers are the people of the company: engineers on other projects and staff who are not technical. They are
not agents and not the stream leads. Sami presents it, so it must be right without his edits. You are the editor:
subagents draft and review, and you own what goes out.

## Steps

1. **Fix the window and the shape.** One Pacific cutoff for every contributor. A Monday recap of the past week
   starts the preceding Monday; a daily standup starts after the last standup that was presented, because "this
   Monday" is not the past week. A page gets charts and a link; a daily or chat recap is a message in the shape at
   the end of `references/page.md`, with no charts. Read the last recap, so nothing in it returns as new.
2. **Draft each stream in parallel, with fresh agents**, one per stream, from the stream table in the chief of
   staff's goals document. Pull the window's merged pull requests once with `scripts/merged_prs.py` and give every
   drafter that file, because GitHub search has one rate limit for the whole box. Brief:
   `references/drafter-brief.md`. Keep your own list of expected streams, their lead and their draft path, because
   a missing draft otherwise reads as a stream that delivered nothing.
3. **Write version 1** in the shape of `references/page.md`, then build it and its charts, because the readers must
   see what the audience will see.
4. **Review with fresh agents in parallel** (`references/reviewer-briefs.md`): a reader who is not technical, an
   engineer from another project, a fact check, a check against Sami's past feedback
   (`references/sami-feedback.md`), and a prose check. Give them the rendered page, not the text alone.
5. **Resolve every finding yourself.** A reviewer that returned is not evidence that the page is ready. Fix every
   factual error, unexplained word, comparison with nothing behind it, and item with no value to the reader. A
   reader reports confusion; a reader does not supply a number, a cause or a claim, so check any fact it proposes
   against the source. Send the whole rebuilt page to new reviewers, with a list of what changed. After three
   rounds, deliver with the unresolved defect named, rather than calling the page checked.
6. **Look at the page before it goes.** Rebuild, screenshot it whole, and read every chart as an image: a
   corrected markdown file does not change a served chart, and a page that builds can still be unreadable.
7. **Deliver where Sami reads:** a reply in the standup room with the link, and at most three lines on how it was
   checked and what was left out. An artifact on an issue is not delivered.

If Sami asked where his own attention went, give one analyst `references/attention.md`. It goes to him alone,
never on the team page.

## Rules

- **Report results, not activity.** An item is something merged, released, measured or decided in the window, with
  a link that proves it, because "value delivered" is what he asks for and work in progress is not it.
- **Fixes to our own mistakes and the agents' own process are not results**, because he reads them as make-work.
  Agent tooling counts only when a person at the company feels its effect; say the effect.
- **The first line of each item is the result for the company, in words every reader knows.** No agent name, issue
  key, PR number, code name, or person the team does not know, because "nobody knows what this means" is his most
  repeated complaint.
- **Define an unavoidable term where it is first used**, because a definition further down does not help the
  summary.
- **Give each number its meaning, its population, its period and its baseline or target**, because two numbers can
  compare different things while looking like a before and after. Say whether the result meets the goal.
- **Read every number from its source yourself**, because a number repeated from a message has been wrong before.
  When a number went down, say why.
- **Measure a merged change on the same inputs, old against new.** Never hold a result back because production has
  not adopted it: merging is what puts code into production.
- **Attribute another person's work to them**, because a company total is not evidence that our agents delivered
  it. Their number may appear as a labelled comparison.
- **State each missed goal with its date and where we are**, because a list of wins hides the main failure.
- **Decisions that need Sami go in the private note, never under blockers**, because the recap is not a queue of
  requests to him. A to-do is not a blocker.
- **Claim only what the evidence shows**, because a proof against a specification is not proof of every property.
- **Say a shared event once**, because repeating it in each stream crowds out other results.
- **Use Pacific dates and mark each weekend item**, because the reader must see what was done at the weekend. Keep
  a billing day in the timezone its bill uses and label it, because a daily total cannot be converted by renaming
  its heading.
- **Give each chart one claim, a title that states it, whole-number axes for counts, and a label on missing data**,
  because an unlabelled gap reads as a zero.
- **Draft within an hour of the standup**, because a recap written at 05:00 for a 10:00 standup misses the morning.
