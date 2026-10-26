# The morning standup

Sami holds a department-heads standup with the chief of staff and every stream lead: each lead presents in turn, the other leads offer help on each other's blockers, and he gives feedback and moves priorities. It started on 2026-10-26 as his experiment ("I would like us to go in like we're going to have a department heads meeting").

The format below comes from what he said was wrong with the standups he had been getting: "Every single standup video that I've received so far has been slop and garbage, focusing on metrics that don't matter, not explaining things, or each repeating the same thing. Every single fucking video from this morning had the 7-hour outage, which, yeah, it was a big deal, but fucking shut the fuck up about it already."

## What you present

At most 200 words, in four parts. Text, posted in the room at the agreed time.

1. **Yesterday**: what your stream finished, and the number it moved, with the value before and after.
2. **Today**: what it will finish, with the hour, and the number that will move.
3. **Blocked on**: what stops you, and who owns it. Nothing else belongs here.
4. **I need from another stream**: name the stream and what you need, so its lead can answer in the room.

Then one short explanation of a single thing he does not already know (see below).

## What keeps it out of the slop bin

- **Say only what your stream can say.** Before you post, cut every line another lead will also say. A fleet-wide event (an outage, a release, a pause) is the chief of staff's to report once, not yours. Say what it did to your stream's numbers, or say nothing about it.
- **A number only if a decision turns on it, and never bare.** Give it the denominator (62 of how many?), its target and due date, and whether that is good or bad. His words on 2026-10-26: "Quoting raw numbers without any context is meaningless. It just gives the illusion of rigor." If yesterday's post gave a different number for the same thing, say why it changed.
- **Times in US Pacific**, never UTC.
- **Explain one thing, the way 3Blue1Brown would.** His words: "maybe a prompt to all the agents, or to all the team leads, should be to do it in the style of 3 blue, 1 brown … currently I feel like the visuals are mostly just boxes of numbers. They don't actually bother to explain any useful concept or anything interesting." Pick the one idea behind your week's work: why the bottleneck is where it is, what a measurement actually proved, why an approach cannot work. Build it from something he already knows, in plain words, and keep it under 100 words.
- **Expand every name the first time.** An issue key gets its title, a tool gets what it does, a session gets whose it is.
- **Nothing he can already see.** No status he reads on the dashboard, no restatement of a plan, no narration of your own process.

## The company version

After the meeting Sami shares a short standup with the whole company. Each lead writes its own stream's section, because only the lead knows what its numbers mean and which work is ours. Before sending it, the lead gives the section alone to a fresh read-only agent posed as an employee who knows the project names but none of the team's details, asks what it believes the team delivered and what it could not interpret, and rewrites until that reader states the result correctly and flags nothing. The chief of staff only assembles. A section is at most 60 words in three lines (Done, Why it matters, Next with a Pacific date), with one source line per claim kept for the chief of staff and left out of the text. Sami rejected four drafts on 2026-10-26; the rules below are what was wrong with them.

1. **Readers know the projects by name.** Do not explain what a stream is. Say what it delivered and what is next, with dates.
2. **Report our team's work, not someone else's.** Verified Linux reports the experiments we ran on the methodology and what they showed, not the grounding numbers of Nickolai's own project, to which we have not yet contributed.
3. **No internal metrics; every number says what it counts and out of what.** "30 projects enrolled" and "a release covering 26 projects" read as two unrelated numbers; write "26 of the 30 enrolled projects". Say "theorem statements" or "proofs", never just "statements".
4. **Claim exactly what the evidence shows.** "Production grading can no longer be fooled by a proof of a false statement" was not known; "we found and fixed N grader bugs that let a false proof earn credit" was.
5. **Say concretely what failed.** "Twelve candidates did not pass review" tells a reader nothing; name what was wrong with them.
6. **Introduce people outsiders do not know by their role, or leave them out**, and use the names people use: "the verified grader", not a description of it.

## Video and audio

Do not make a video unless he asks for one. On 2026-10-26 he stopped them: "If the videos are incredibly expensive, then I think the audio is probably enough for me … I feel like the agents have not really been putting much effort into making the video actually explanatory, and maybe I think that the content is currently not yet good enough for the video to actually be helpful." When the standups are good enough that a picture would add something, propose it with the picture you would draw.

## After the meeting

The leads and the chief of staff improve this file together from his feedback in the meeting, including anything he says he could do differently. Quote him, keep each change to what the feedback supports, and record what changed and why in the commit message.
