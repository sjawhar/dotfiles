---
name: taiga-requests
description: "Use before you ask a person for anything to do with Taiga (taiga.ant.dev): platform or environment access, an image registry grant in gcp-taiga, a bigger or faster grader machine, a quota or storage limit, a change to how Taiga grades, logs, restores or times out, a platform bug report, or a question about Taiga's behaviour. Also when you draft a Taiga support request, a message to the Anthropic Slack channel, or any ask that contains the word Taiga."
---

# Taiga requests

If this skill disagrees with the code, a command's output or its `--help`, follow those and say which line of this skill is wrong.

Taiga (<https://taiga.ant.dev>) is Anthropic's evaluation platform. We are one vendor organization on it. Anthropic runs the platform and owns the Google Cloud project `gcp-taiga`, which holds the image registry. We own our side: our environments, problems, problem versions, jobs, runs, images, uploaded files, QA jobs, and the membership of our own Google groups.

Most requests agents send about Taiga are answerable from the wiki or doable by us. Route the request before you write it.

## Answer it from the wiki first

- The wiki is a copy in this repository: `~/src/binary-equivalence-taiga/.claude/skills/taiga-wiki/` (`skill://taiga-wiki` inside that repo). Read it before you ask any person how Taiga behaves, because a person has to look it up in the same place.
- Measure the behaviour yourself when the wiki is silent: a probe job, a run's API record, or a log you add. A measured fact beats an answer you wait a day for.
- A fact that is still missing is a question for Anthropic, not for Sami: he reads the same wiki.

## Do it yourself

- Call the API through `taiga_client` or the `taiga` command (`~/src/binary-equivalence-taiga/taiga/client/README.md`), never with a raw token, because one shared request budget and request log keep lanes from throttling each other.
- Add users, set roles and grant environment access in Access Control (<https://taiga.ant.dev/access-control>) or its API as an org admin of our organization. No request to Anthropic is needed for any of it.
- Get more telemetry by logging inside our own image and downloading it. Taiga is not changed for us, so a request for new platform timestamps or metrics buys nothing.
- Free environment storage by deleting our own files (the cap is 400 GB per environment, shared by every lane). Taiga keeps a file while any problem version names it, so delete the versions first.
- Pick a machine shape from the list Taiga offers (`~/src/binary-equivalence-taiga/scripts/deploy/vm_shapes.json` is the committed snapshot of it). A shape outside that list does not exist to ask for.
- Pull images as an account that already holds registry access; `~/.dotfiles/plugins/sjawhar/skills/using-gcloud/references/taiga-access.md` lists the four and the measurement. Nobody at Theorem can grant a role on `gcp-taiga`, so a grant request there has no recipient.

## Ask our own people

- Google group membership (`anthropic-taiga-users@theorem.dev` for sign-in, `anthropic-taiga-engineers@theorem.dev` for pushing and pulling images) is ours to manage: ask Archana, who administers our Google Workspace. The list of accounts was settled on 2026-10-19 and closed on 2026-10-20, so bring the case for why no existing account fits.
- An environment or problem another lane owns is that lane's, not the platform's. Ask the owner in Dispatch or Envoy and read `skill://dispatch-first` first, because the answer is often already recorded.

## Ask Anthropic, and only through a person

Anthropic alone can raise a per-organization limit, change the platform, or add an account to a repository they bind. That route is a support request in the shared Slack channel, which only a person in Anthropic's workspace can send, so it costs a named human's time and days of waiting.

- Send one only when our own side is already done and the work still cannot proceed, and say in the request what we did first.
- Keep the request to one message with the environment name, the number we need, and the evidence. Do not send a second message for the same topic.

## Never route these to a person

- A platform change: a new timestamp, a different restore mode, a wider or faster grader machine, a scheduler change. The platform does not change for us, and a bigger machine is not a speed result.
- A note telling Anthropic about a defect you found in grading, QA or restores. Fix our side; the fix is what the work needed.
- An IAM grant on `gcp-taiga`, for any account or any role.
- A question the wiki, the API or a probe answers.
- A request to raise a limit before we have deleted or moved our own share of what fills it.

## Before any Taiga ask reaches a person

Each of these refuses most drafts, so check them in order and drop the ask when one refuses it.

1. Name the owner of the thing you want changed: us, Archana, or Anthropic. If the owner is not a person, there is no ask.
2. State what you already did on our side, with its result. An ask with nothing on this line is premature.
3. State what stays blocked after the ask is refused. If nothing is blocked, do the unblocked work instead.
4. For a message that leaves the company, follow `skill://outside-message` and `~/.dotfiles/.claude/CLAUDE.md`: draft it, show it, then send.
