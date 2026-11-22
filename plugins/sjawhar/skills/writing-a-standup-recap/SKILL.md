---
name: writing-a-standup-recap
description: "Use when writing a standup recap (a page, a short chat update, or the standup lines of a Legion controller, the agent that runs one project's issue queue), a daily or weekly summary of delivered work for the team, or an answer to 'what did we ship'. Not for agent-to-agent status."
---

# Writing a standup recap

Sami presents the recap, so it must be right without his edits.

The readers are the people of the company: engineers on other projects and staff who are not technical. They are not agents, and not the Legion controllers who meet Sami in the standup room. You are the editor: subagents draft and review, and you own what goes out.

## Steps

1. **Fix the window and the shape.** End the window at one Pacific time for every contributor: the time you start the drafters.

   | Recap | Window starts |
   | --- | --- |
   | A Monday recap of the past week | The preceding Monday |
   | A daily standup | After the last standup that was presented |

   | Recap | Form |
   | --- | --- |
   | A weekly page | A page with charts and a link |
   | A daily recap, a chat update and a Legion controller's standup lines | One message in the short shape that `references/page.md` describes, with no charts |

   Read the last recap of the same shape (a weekly page or the short shape), so nothing in it returns as new.
2. **Pull the window's merged pull requests once** with `scripts/merged_prs.py` (its docstring has an example call). GitHub search has one rate limit for the whole machine, so every drafter reads that file instead of searching.
3. **Start one fresh drafter per stream,** in parallel, from the stream table: the table of Sami's work streams, each with its lead and its stream issue, that the chief of staff keeps. The chief of staff is the agent that sets the order of his streams and checks in with each lead daily. `dispatch search --query '"Stream issue" "Main numbers"'` finds the table.
   - Read `references/drafter-brief.md` when you write their brief.
   - Keep your own list of expected streams with each lead and draft path, so a missing draft does not pass as a stream that delivered nothing.
4. **Write version 1** in the shape of `references/page.md`, then build the page and its charts.
5. **Review the rendered page,** not the text alone, with fresh agents in parallel. Read `references/reviewer-briefs.md` when you dispatch them.
6. **Resolve every finding yourself:** every factual error, unexplained word, comparison with nothing behind it, and item with no value to the reader. A reviewer that returned is not evidence the page is ready.
   - Check any number, cause or claim a reviewer proposes against its source before you use it.
   - Send the whole rebuilt page to new reviewers with a list of what changed.
   - After three rounds, deliver with the unresolved defect named.
7. **Check the served page.** Rebuild, screenshot the page whole, and read every chart as an image. A corrected markdown file does not change a served chart.
8. **Deliver where Sami reads:** a reply in the standup room, the Dispatch message thread of his morning meeting with the chief of staff and the Legion controllers. Find its opening message with `dispatch search --query 'department-heads standup room'`, then reply with `dispatch message --issue <KEY> --in-reply-to <message id>`. The reply holds the link and at most three lines on how it was checked and what was left out. An artifact on an issue is not delivered.

If Sami asked where his own attention went, read `references/attention.md` and give it to one analyst. That result goes to him alone, never on the team page.

## Rules

The rules reviewer checks the draft against each rule here, one at a time.

### What goes in

- Results, not activity: something merged, released, measured or decided in the window, with a link that proves it. Work in progress, narration of process, and fixes to our own mistakes or to the agents' own process are not results.
- Agent tooling counts only when a person at the company feels its effect.
- For agent tooling that counts, say the effect.
- Every delivered result is gathered: ask each stream's lead what it delivered rather than guess.
- The page then holds the most important delivered results, within the counts `references/page.md` sets.
- The delivery lines name what was left out.
- Only what is new since the last report: a number already reported is not a new gain.
- If nothing was delivered:
  - say so plainly;
  - say why.
- Another person's work is theirs: a company total is not evidence that our agents delivered it.
- Another person's number may appear as a labelled comparison.
- Each missed goal, with its date and where we are.
- For missed goals, use only the targets and dates Sami set: no softer target, and no later date in place of his.
- What actually ships today ("This week" on a weekly page), arranged in advance, not an intention and not redefined at each check-in.
- Only real blockers. A to-do is not a blocker.
- Sami is never the blocker: a decision that needs him goes to him alone as a Dispatch ask (`dispatch ask`, unless one is already open on it), never on the page.
- A shared event once, in one section.
- Nothing the reader already knows:
  - nothing the last recap says;
  - nothing an earlier section of the page says, except a result "The week in brief" names and its section explains.
- No claim stronger than the evidence (a proof against a specification is not proof of every property).
- No rule Sami did not set.

### Words

- The first line of each item is the result for the company.
- Every line uses only words every reader knows: no internal name, code name, agent name, issue key, pull request number, internal unit (CPU seconds, "objects") without what it means, or person the team does not know.
- For a person the team does not know, which is anyone from outside the company, give their role and organization, not their name.
- Define an unavoidable term where it is first used.
- Explain, do not recite: each claim carries its reason or its mechanism.
- Short: the page reads top to bottom in five minutes.
- High level: each item says what changed for the company, not how it was built.
- Write in plain controlled English, ASD-STE100 (the `asd-ste100` skill).
- Fewer items said clearly beat more items.

### Numbers

- Give each number its meaning, its population, its period and its baseline or target. Two numbers can look like a before and after while measuring different things.
- Say whether each number meets the goal.
- Open each number's source yourself (the record, pull request or measurement it comes from), never a message that repeats it.
- Keep each number's source link beside the number in the draft.
- When a number went down, say why.
- Measure a merged change on the same inputs, old against new.
- Do not hold a merged change's result back because production has not adopted it: merging is what puts code into production.
- Where the data fits, use one of these shapes, though none is required; they read well:
  - a before and after from production;
  - plain scores per attempt, which list in order the score that grading gave each run of the same task;
  - a page whose rows each show a trend at a glance, such as the timeline's row per project (`references/page.md`, Charts).

### Dates and charts

- Pacific dates, never UTC.
- Mark each weekend item.
- Keep a billing day in the timezone its bill uses, labelled: a daily total cannot be converted by renaming its heading.
- A chart only when it explains something:
  - one claim;
  - a title that states it;
  - whole-number axes for counts;
  - a label on missing data (an unlabelled gap reads as a zero).
- No narrated slideshow.
- No boxes of numbers.
- Start the drafters no earlier than one hour before the standup, so the morning's results are in the recap.
