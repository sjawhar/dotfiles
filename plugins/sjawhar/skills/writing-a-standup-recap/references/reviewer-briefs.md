# Reviewer briefs

Run the five reviewers below in parallel, each a fresh agent that did not write the draft. Give each one the draft
path, the chart files (an SVG reads as an image with the `read` tool's `:img` selector: `chart.svg:img`), the
rendered page URL, and this shared context:

> You review a recap that Sami Jawhar presents at the company standup. Readers: the people of the company,
> engineers on other projects and staff who are not technical. Read-only: do not edit the page and do not message
> anyone. Write at most 8 findings, most important first. Each finding: the quoted text, what is wrong for your
> reader, and a replacement sentence or "cut". No praise. Report confusion and error; do not supply a number, a
> cause or a claim of your own, because the editor cannot check what you invent. PASS means no required change
> remains.

Each later round goes to new agents with the whole rebuilt page and a list of what changed: a fixed sentence read
alone hides a chart or summary that now contradicts it. Stop after three rounds.

## Reader who is not technical
You work in operations, finance or recruiting. You know the company's goals and its customer. You do not know what
a proof, a grader, a kernel, CI or a fuzz target is. You see the page once, on a shared screen, for five minutes.
Flag every word you would not understand, every item whose value to the company is unclear, and every chart you
cannot read in five seconds. Then write each section's point in your own words. End with what you would cut first
if the page had to be a third shorter.

## Engineer from another project
You know proofs, builds and CI, but you have not followed this work. Flag claims that oversell or are vague (a
number with nothing to compare it with, "faster" without from what), a caveat that is missing or buried, low-level
detail nobody outside the work needs, and anything that reads like the agents' own process. Check each chart: the
title states the point, the axis is honest, the point is obvious. Check that the brief and the sections agree.

## Fact check
Use a reviewer from another model family, which does not share the drafter's blind spots. For a part that carries
HDO material as `skill-librarian`'s Rules define it (`skill://skill-librarian`), use a fresh Claude reviewer
instead: HDO material never goes to another model family.
Check every number, date and weekend label against every stream draft and the chart script. For each: OK, or
WRONG or OVERSTATED with the source text and a corrected sentence. Open the cited record itself when a claim is
load-bearing (an item's result or number rests on it) or unsupported, or when two drafts disagree: agreement with a
draft is not evidence. Flag a claim with no support, a wrong weekend label (Saturday and Sunday, Pacific), a
billing figure whose days are UTC but read as Pacific, a chart whose dates disagree with the text, and a dropped
caveat that changes the meaning ("not in production", "measured on 3 cases", "its definition changed this week").
Then check each conclusion against its evidence: a measurement at one size or of one population said to hold for
another, a count whose population differs from the one the sentence names ("waits on you" for every open ask), a
forecast written as a present count, a guessed cause stated as fact.

## Rules check
Apply each rule in the "Rules" section of `skill://writing-a-standup-recap` to the draft, one at a time. For each:
PASS, or FAIL with the line that fails it and the fix.

## Prose check
Read `skill://ce-noslop` and `skill://plain-words` first. Audit for AI tells, filler, unsupported connective
claims, sentences over 25 words, passive voice that hides who did the work, and the same thing named two ways.
Give a rewritten sentence for each. Keep every fact; add none.
