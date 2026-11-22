---
name: making-explainer-videos
description: Use when making an explainer video (a narrated video on what a work stream shipped, how a design or system works, a concept, or a question someone asked), when writing or reviewing an explainer's script, when choosing its voice, or when asked to remake one.
---

# Making explainer videos

An explainer teaches the viewer what a dashboard cannot: how a thing works and why it gives the result it gives. The viewer is the person who asked for it: an experienced software engineer who has not followed this project's details and is new to its field.

**Done when:** you have played the delivered video (step 6) on the dashboard's front page, from the video dropdown beside its stream, before you report it.

## What the video is

1. **The answer, in the first 10 seconds.**

   | Video | Answer |
   | --- | --- |
   | For a question | The answer in one sentence |
   | For a stream update | What changed and the number it moved, before and after |

2. **The mechanism, shown:** the diff or code that changed, the system running (a screen recording), a diagram that builds as the narration explains it, an example worked through.
   - Say what the work does and why that gives the result: a recitation of state ("five task types", "17 spec versions", "jobs running") explains nothing.
3. **The evidence for each claim.**
   - Every number carries its unit, its date and its source, on screen.
   - Cut a claim with no evidence.
   - Every comparison names what it is compared with: "the same verdict as the unmodified program", not "the same verdict".
4. **What it changes, and what comes next, in the viewer's terms:** what it makes possible, the next target and its date.
   - Speed is wall-clock time, not CPU time.

- A stream update covers what merged since that stream's last video, items 2 to 4 for each change.
- A video about a question answers the question as it was asked.
- If the work cannot answer a video's question yet, say what is missing and what will settle it.

Leave out what the viewer already knows: their priorities and targets, which streams and leads exist, the rules they set, and anything the last video said.

## Words

- Each term is plain English, or defined in one plain sentence where it first appears.
  - A label from inside the work (a lane code, a run name, an internal tool) comes after that sentence, or not at all.
- **A rule or requirement attributed to the viewer:**
  - Check it against their own words at the source.
    - The sources are the session transcript, Slack, and Dispatch.
    - Dispatch is the issue tracker that holds each stream's issues, asks and comments.
    - Dispatch's commands are `dispatch search --query '<words>'`, then `dispatch read`.
  - Quote it.
  - Do not restate it more strictly.

## Form

- **A video, not a narrated slideshow.**
  - Something moves the whole time.
  - No frame holds still for more than 3 seconds.
  - Use screen recordings of the real systems, diagrams that build step by step, and charts of the real numbers.
- At most 5 minutes.
- **Voice:** ElevenLabs "Max" (voice id `Gfpl8Yo74Is0W6cPUWWT`) on `eleven_v4`, at about 210 words a minute, unless the viewer chose another.

## Tools

- **Animation:** [Revideo](https://re.video) (TypeScript scenes rendered to MP4 by headless Chrome and ffmpeg).
  - Pick an earlier explainer's Revideo project that has what you need (charts drawn from data, diagrams that build node by node, terminal clips timed to the narration, voice timing): the Verified Linux explainer's project has all four.
  - Run `dispatch search --query 'Verified Linux explainer Revideo project'` to find the host and directory that keep the Verified Linux explainer's project.
  - Copy an earlier explainer's Revideo project into your own directory: that explainer's `<subject>.src/README.md` names the project and its rebuild command.
  - Change the copy.
- **Screen recordings:**

  | Screen | Record with |
  | --- | --- |
  | Browser pages | The `browser` tool's recording, or Playwright |
  | Terminals | asciinema and `agg` |

  - Record the real system.
  - Cut hard to the action.
  - Keep each clip's wall-clock and file duration for the review.
- **Render** on a cloud VM you create for it, not on a shared box.
  - Delete the VM when the video ships.
- **Voice:**
  - Put the tag `[very fast, rapid-fire delivery]` before each line: `eleven_v4` has no speed setting.
  - Then speed the audio with ffmpeg `atempo` to about 210 words a minute: `eleven_v4` has no speed setting, and the tag gives Max about 183 words a minute.
  - Measure each line's words a minute from the audio.
  - `.strip()` the API key (`secrets ELEVENLABS_API_KEY -- ...`): a trailing newline in the key breaks the request's header.
  - Do not print request headers.

## Order of work

1. **Write the script:** per section, the facts with their sources, then the narration.
2. **Red team.**
   - Have a fresh agent red-team the script against this skill: claims without evidence; steps skipped or terms unexplained; anything the viewer already knows; drift from what was asked; rules the viewer never set.
   - Fix every finding.
3. **Narration checks.** Run the product-demos skill's narration checks on the fixed script: lint to 0 hard violations, then the teach-back.
4. **Build.** Build the video under the product-demos skill's "Production rules" (pad the audio, not the video).
5. **Review.**
   - Review the finished MP4 as the product-demos skill's "Review before it ships" section says, with every check it lists. Its strategy and vision reviewers run in parallel:

     | Reviewer | What it does |
     | --- | --- |
     | Strategy | Asks whether the video teaches the thing, what can be cut, and what the narration claims that the screen does not show |
     | Vision | Samples a frame every 2 to 3 seconds, reads the screen, and flags error text, text cut off at the frame's edge, every frame that holds still for more than 3 seconds, and every empty frame (one that shows only a background, with no text, chart, diagram or recording) |

   - Fix every CUT.
   - After you fix a CUT, re-review each boundary the fix moved.
6. **Deliver.**
   - Upload the video and its script to the stream's Dispatch issue as these artifacts:

     | Artifact | Name |
     | --- | --- |
     | The video | `explainer-<subject>.mp4`, with `<subject>` in lowercase letters, digits and dashes |
     | Its script | `explainer-<subject>-script.md`, with front matter `title: <the title the viewer sees>` |

     The streams dashboard (`chief-dashboard/`) plays the latest version of every such artifact on a stream issue under that title.
   - One video per subject: a correction is a new version of the same artifact, not a second name.
   - Keep `<subject>.src/` in your own directory, beside your copy of the Revideo project: script, narration audio, clips, and the exact rebuild command.

- Run at most 3 rounds of any check here (red team, teach-back, video review), because agent checking agent does not converge on its own.
  - Each round re-reads only what changed.
  - Each round may not raise a finding an earlier round raised.
  - After the third round, ship it.
  - After the third round, write what is still weak in the video's `.src/README.md`.
- If you own the subject, make the video, or have a subagent make it.
- Do not wait on a question to the viewer to make the video.
- While the work the video describes has open tasks, give the video to a subagent, so the video takes no time from that work.
- While the work the video describes has open tasks, keep working on those tasks, so the video takes no time from that work.
