---
name: making-explainer-videos
description: Use when making an explainer video, a narrated video that explains what a work stream shipped, how a design or system works, a concept, or a question someone asked; when writing or reviewing an explainer's script; when choosing its voice; or when asked to remake one.
---

# Making explainer videos

An explainer teaches the viewer what a dashboard cannot: how a thing works and why it gives the result it gives. The viewer is the person who asked for it: an experienced software engineer who has not followed this project's details and is new to its field.

## What the video is

1. **The answer, in the first 10 seconds.** For a question: the answer in one sentence. For a stream update: what changed and the number it moved, before and after.
2. **The mechanism, shown.** How the thing works: the diff or code that changed, the system running (a screen recording), a diagram that builds as the narration explains it, an example worked through. A recitation of state ("five task types", "17 spec versions", "jobs running") explains nothing; say what the work does and why that gives the result.
3. **The evidence for each claim.** Every number carries its unit, its date and its source, on screen. A claim with no evidence is cut. Every comparison names what it is compared with: "the same verdict as the unmodified program", never "the same verdict".
4. **What it changes, and what comes next.** In the viewer's terms: what it makes possible, the next target and its date. Speed is wall-clock time, never CPU time.

A stream update covers what merged since that stream's last video, items 2 to 4 for each change. A video about a question answers the question as it was asked. If the work cannot answer it yet, say what is missing and what will settle it.

## What the viewer already knows

Leave it out: their priorities and targets, which streams and leads exist, the rules they set, and anything the last video said.

## Words

- Each term is plain English, or defined in one plain sentence where it first appears. A label from inside the work (a lane code, a run name, an internal tool) comes after that sentence, or not at all.
- A rule or requirement attributed to the viewer is checked against their own words at the source (transcript, Slack, Dispatch) and quoted, never restated more strictly.

## Form

- A video, not a narrated slideshow: something moves the whole time, and no frame holds still for more than 3 seconds. Use screen recordings of the real systems, diagrams that build step by step, and charts of the real numbers.
- At most 5 minutes.
- Voice: ElevenLabs "Max" (voice id `Gfpl8Yo74Is0W6cPUWWT`) on `eleven_v4`, at about 210 words a minute, unless the viewer chose another.

## Order of work

1. Write the script: per section, the facts with their sources, then the narration.
2. A fresh agent red-teams the script against this skill: claims without evidence; steps skipped or terms unexplained; anything from "What the viewer already knows"; drift from what was asked; rules the viewer never set. Fix every finding.
3. Run the product-demos skill's narration checks on the fixed script: lint to 0 hard violations, then the teach-back.
4. Build, review and deliver as [production.md](references/production.md) says.
5. One video per subject. A correction replaces the video under the same name.

At most 3 rounds of any check here (red team, teach-back, video review). Each round re-reads only what changed and may not raise a finding an earlier round raised. After the third, ship it and write what is still weak in the video's `.src/README.md`. Reason: agent checking agent does not converge on its own. One pull request at Anthropic ran 50+ review rounds; a 3-round cap with change-only re-review took one-round passes from 65% to 78% (claude.dev, 2026-10-15). The first video made with this skill ran 36 teach-back rounds and 3 review rounds in one night.

The person who owns the subject makes the video, or has a subagent make it. It never waits on a question to the viewer, and never takes time from the work it describes.
