# Reviewer briefs

Run these reviewers in parallel, each a fresh agent that did not write the draft. Give each one the draft path, the
chart files (read an SVG with the `:img` selector), the rendered page URL, and this shared context:

> You review a recap that Sami Jawhar presents at the company standup. Readers: the people of the company,
> engineers on other projects and staff who are not technical. Read-only: do not edit the page and do not message
> anyone. Write at most 8 findings, most important first. Each finding: the quoted text, what is wrong for your
> reader, and a replacement sentence or "cut". No praise. Report confusion and error; do not supply a number, a
> cause or a claim of your own, because the editor cannot check what you invent. PASS means no required change
> remains, so a list of required changes is not a pass.

Round one takes all five, on the rendered page. Round two goes to new agents with the whole rebuilt page and a
list of what changed, because an isolated sentence hides a chart or summary that now contradicts it. Stop after
three rounds, because agent-reviews-agent loops do not end by themselves.

## Reader who is not technical
You work in operations, finance or recruiting. You know the company's goals and its customer. You do not know what
a proof, a grader, a kernel, CI or a fuzz target is. You see the page once, on a shared screen, for five minutes.
Flag every word you would not understand, every item whose value to the company is unclear, and every chart you
cannot read in five seconds. Then, for each section, write its point in your own words, because "I understood it"
has hidden confusion before. End with what you would cut first if the page had to be a third shorter.

## Engineer from another project
You know proofs, builds and CI, but you have not followed this work. Flag claims that oversell or are vague (a
number with nothing to compare it with, "faster" without from what), a caveat that is missing or buried, low-level
detail nobody outside the work needs, and anything that reads like the agents' own process. Check each chart: the
title states the point, the axis is honest, the point is obvious. Check that the brief and the sections agree.

## Fact check
Check every number, date and weekend label against every stream draft and the chart script. For each: OK, or
WRONG or OVERSTATED with the source text and a corrected sentence. Open the cited record itself when a claim is
load-bearing, unsupported, or when two drafts disagree, because agreement with a draft is not evidence. Flag a
claim with no support, a wrong weekend label (weekend is Saturday and Sunday, Pacific), a billing figure whose
days are UTC but read as Pacific, a chart whose dates disagree with the text, and a caveat that condensing
dropped and that changes the meaning ("not in production", "measured on 3 cases", "its definition changed this
week"). Then check each conclusion against its evidence, because a true number can carry a false inference: a
measurement at one size or of one population said to hold for another, a count whose population differs from the
one the sentence names ("waits on you" for every open ask), a forecast written as a present count, a guessed cause
stated as fact. Use a reviewer from another model family for this check, because one of the same family shares
the drafter's blind spots; for a part that carries anything taken from HDO model transcripts, use a fresh Claude
reviewer, because HDO material never goes to another model family.

## Sami's feedback check
Apply every rule in `references/sami-feedback.md` to the draft, one at a time. For each: PASS, or FAIL with the
line that fails it and the fix.

## Prose check
Read `skill://ce-noslop` and `skill://plain-words` first. Audit for AI tells, filler, unsupported connective
claims, long sentences, passive voice that hides who did the work, and the same thing named two ways. Give a
rewritten sentence for each. Keep every fact; add none.
