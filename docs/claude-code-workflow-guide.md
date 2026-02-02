# How I Use Claude Code

## The Core Idea

Most people don't plan enough. That's my hypothesis for why some get dramatically more leverage from Claude Code than others. I don't know if it's the key difference, but I think it makes a big difference. Most plans have giant holes no one bothered to think through. I've watched engineers reach a point where they sense there's something they haven't figured out, but they're tired of thinking, so they just start building. That almost always comes back to bite them. The uncomfortable truth is that planning is hard, it takes effort, and we've developed a collective habit of skipping the hard parts because "I thought about it, we're fine."

The problem usually isn't that people know there's a hole and choose to ignore it—it's that they don't see the hole at all. We walk around not seeing that we haven't thought things through. The structured planning tools help here because they force you to articulate assumptions. When Claude asks "you mean X?" and you say "no, actually," you've just avoided starting implementation with a fundamental misunderstanding. The value isn't that Claude is smart—it's that the process forces you to think.

The return on planning compounds in ways that aren't obvious until you experience them. A detailed plan lets you delegate confidently—you can kick off an agent, walk away for two hours, and come back to something that actually works. A vague plan means constant babysitting, frequent course corrections, and the feeling that you might as well have written the code yourself. Better plans create trust, and trust enables parallelism. When you can run three agents simultaneously because you know each one has clear instructions and a way to verify its work, your throughput multiplies. When your plans are weak, you're stuck reviewing one thing at a time, and the whole system bottlenecks on your attention.

This leads to the second shift: verification is part of planning. The goal isn't to understand every line of code the agent writes—it's to have evidence that the code does what you need. Build verification into the plan from the start. Define what "done" looks like in concrete, testable terms. Make it really clear—both in your own head and to the agent—what it means to know that something is working. Tests are good, but agents and humans both are pretty bad at writing good tests. So what's your ground truth? Can the agent compare its output against known-good data? Can it interact with a frontend? If you're building a TUI, can it take screenshots with tmux, send keystrokes, and verify the output itself? The more concrete your verification criteria, the more confidently you can delegate.

## Getting Started

Run Claude Code inside a dev container. This is your security baseline—it isolates the agent from your host system and prevents accidental damage to your files or credentials. Never use `--dangerously-skip-permissions` outside a container.

Install these plugins to unlock structured workflows:

- **[superpowers](https://github.com/obra/superpowers)** – Skills for planning, execution, and code review
- **[compound-engineering-plugin](https://github.com/EveryInc/compound-engineering-plugin)** – Brainstorming, plan review, and knowledge compounding

Each plugin's README covers installation. For a working example of a complete setup, see [sjawhar/dotfiles](https://github.com/sjawhar/dotfiles).

## The Workflow

The core loop has three phases: plan, execute, and review. Verification isn't a separate phase—it's built into the plan itself.

### Plan Thoroughly

Start with `/brainstorm` to explore the problem space. This phase asks probing questions, surfaces edge cases you haven't considered, and clarifies intent before you've invested in a direction. The structured questioning matters because people often don't see the holes in their own thinking.

Once you have clarity, use `/writing-plans` to turn ideas into a detailed spec. Sometimes I'll use `/deepen-plan` if I need more research or depth, but not always. Then run `/plan_review` to catch bugs and remove unnecessary complexity before any code gets written. This step is particularly valuable because Opus loves to add extra features that don't need to be there—the plan review agents focus on simplicity and strip that out. I also ask a lot of my own questions during this phase. Lots of iteration happens here.

The output is a spec file written to disk—not just conversation history. This matters because plans get passed to fresh sessions for execution, avoiding the context rot that accumulates when you iterate in a single conversation.

**Give complete information.** A common mistake is leaving out relevant context because "Claude can figure it out." Maybe—but you've just stacked compound penalties. If you're 70% confident on one assumption and skip context that would clarify it, and then you do the same for two more assumptions, you're now at 70% × 70% × 70% = 34% chance of getting the combination right. Don't make Claude guess things you already know. The work of gathering and providing context pays for itself.

### Compound Your Learnings

Use `/compound-docs` (from compound-engineering) to document solutions as you discover them. It creates an indexed local database of learnings and best practices. When you later use `/deepen-plan`, it automatically searches this database—or you can dispatch researcher agents yourself to find relevant past solutions.

### Build Verification Into the Plan

Before you start executing, be crystal clear about what "working" means. Can the agent compare output against ground truth data? Can it interact with a frontend or browser? If you're building a TUI, can it take screenshots with tmux, send keystrokes, and verify the results? Tests are good, but agents (and humans) are bad at writing tests that actually catch problems—the agent can always write tests that pass without the thing actually working. Give it something concrete to check against.

**Example: time tracking with ground truth.** I built a time tracking system where every UI click and scroll logs an event to a JSON file. The agent can cross-reference this against Claude Code session history to show time per project, human time vs. agent time, and actual parallelism rates. No manual tracking required—just instrumentation the agent can query. That's the kind of verification that lets you trust results without reading every line of code.

Useful tools for verification: `/agent-browser` (from compound-engineering) lets the agent interact with web pages directly. The `context7` MCP tool is good for fetching documentation. The `gh` CLI lets agents interact with GitHub issues and PRs.

### Execute with Sub-agents

Open a new session, point it at your plan file, and use `/subagent-driven-development` and `/test-driven-development`. The agent spawns parallel workers to tackle independent pieces of the spec. While one task executes, you turn your attention to brainstorming and planning the next thing. The pattern repeats.

You can also just tell Claude to "dispatch sub-agents to do X, Y, and Z in parallel" and it will. Sub-agents are useful for more than just implementation—they're a way to manage context. When you have a large chunk of data to process or a broad codebase to explore, dispatch sub-agents to work on pieces in parallel and have them report back summaries. The orchestrating agent stays focused while the sub-agents do the heavy lifting.

Use git worktrees to run multiple agents in parallel on different features without them stepping on each other. Each worktree is an isolated copy of your repo, so agents can work independently.

### Review, Don't Verify

When execution completes, run `/analyze` to catch code quality issues. This command launches three sub-agents in parallel—a bug finder, a type checker, and a code simplifier—then synthesizes their findings. Sub-agents fix problems directly rather than just reporting them.

Then open a PR and run `/requesting-code-review`. Only sometimes do I read the code myself—maybe one in fifteen PRs gets a detailed manual review. This isn't verification—that already happened during execution via the ground truth you built into the plan. This is just catching style issues, unnecessary complexity, and obvious bugs before merging.

Use a fresh session for code review—don't let context get too full. Many people switch sessions around 70% context usage. A reviewer session that hasn't seen the implementation brings fresh eyes.

## Working with claude.md

Your `claude.md` file is how you teach Claude to write code the way you want it. Every time it makes a mistake—wrong test patterns, unnecessary comments, bad naming conventions—say "No, don't do that. Do this instead" and add it to `claude.md`. Over time, it learns your preferences.

Put project-specific patterns in the repo's `claude.md`. Put global preferences (like "use jj instead of git") in `~/.claude/claude.md`. They're hierarchical—the repo-level file takes precedence for that project.

Some things I've had to tell it repeatedly before it stuck: "don't make test classes," "no banal comments every three lines," "don't add eight-line docstrings to two-line functions." Eventually these become second nature, but you have to be persistent about adding corrections as you encounter them.

## Resisting Scope Creep

When implementation becomes cheap, you'll feel tempted to add things you otherwise wouldn't have bothered with. Sometimes that's fine—you can implement nice-to-have features that weren't economical before. But be deliberate about it. Extra features are still distracting from priorities, still need to be maintained, and still add complexity. The fact that something is easy to build doesn't mean it should be built. Stay focused on what matters.

## Common Questions

### How detailed should my plans be?

Detailed enough that you've thought through all the holes—including how the agent will verify its work. Many engineers reach a point where they're "tired of thinking about it" and defer, but those deferred problems almost always come back. Use structured brainstorming and plan review tools to catch bugs and misunderstandings before implementation begins.

### When do I know a plan is "good enough"?

A plan is ready when you can describe the very first step concretely and have a way to verify it worked. Start with the most basic thing you can test, then build from there. If you find yourself wanting to skip over something you haven't figured out, that's a sign you need to keep planning.

### How do I know the code is correct without reading all of it?

Build verification into the plan, not into your review process. Give the agent ground truth data it can compare against. Let it interact with a frontend or browser. If it's a TUI, give it tmux so it can take screenshots and send keystrokes. The agent should be able to prove the code works during execution—not just write tests that pass. Tests are necessary but not sufficient; agents can always write tests that pass without the thing actually working.

If you're uncertain about a specific piece, ask Claude to explain what it does, then ask whether it achieves your goal. You don't have to understand the implementation to verify the outcome.

### How do I create custom skills?

Use `/writing-skill` from superpowers. It's great because you can try out a skill, then open a new session and point Claude at that session: "use the writing skills skill to improve this skill." The skill improves itself iteratively.

### How many agents can I run at once?

Experienced users run 6-8 Claude Code instances simultaneously. Each instance can also launch sub-agents in parallel. The practical limit depends on your machine's memory. When you only have two agents running, you're often sitting around—find more tasks to parallelize.

### How do I manage context switching between projects?

Context switching has different costs depending on your mental state. When things are going well, you can bounce between projects easily. When stuck, focus on one thing. If you've been in a spiral of failures for the past hour, adding more context switches makes it worse.

That said, spending most of your time in planning naturally reduces context-switching burden. You're thinking deeply about problems, then handing off execution. The agent can work for extended periods while you turn your attention elsewhere. It's up to you how much you want to bounce around—but the planning-heavy workflow makes it more sustainable.

### Why should I always use dev containers?

Agents sometimes make surprising decisions. Running inside a container limits the blast radius of any unexpected behavior. If you browse untrusted content or work with external APIs, prompt injection is a real risk—someone could leave a malicious review on Amazon that changes your shipping address if you're using Claude to shop. A container keeps your host system isolated.

### What does `dangerously-skip-permissions` do?

This flag lets Claude Code execute commands without asking for confirmation. It dramatically speeds up workflows but means the agent can run any terminal command automatically. Only use this inside a dev container, never on your host machine.

### What's the difference between skills and agents?

A skill is a way to dynamically fetch relevant context—whenever you start a session, available skills are prepended to the prompt. They're like conditional instructions that activate when you invoke them.

Agents (sub-agents) have their own copy of context. They branch off from the main conversation and work independently. Anything they discover stays in their context window, not the main one. This is useful for exploration—you can send agents to investigate without polluting your main context with irrelevant details.

### How do I handle a task I'm uncertain about technically?

If the technical decisions seem domain-specific and you don't have strong opinions, pause planning and escalate. Post a GitHub issue for the engineering team, or ask someone with more domain expertise. Don't let Claude guess at things that require judgment you don't have. Once you have clarity on the approach, resume planning.

### Should I use hooks for notifications?

Claude Code supports hooks that can trigger notifications when the agent needs input or finishes a task. Some people like this. I don't recommend it—having your terminal ping you constantly is disruptive and erodes the focus that makes this workflow valuable. Be protective of your headspace. The superpowers plugins already handle things like auto-formatting, so hooks add less value than you might expect.

## What's Next

Once you're comfortable with the basic workflow, there are some advanced patterns worth exploring:

### Automation Loops

You can run Claude Code in unattended loops with prompts like "find things to improve in tests." Structure it as a while loop that reads a prompt file and runs with `--dangerously-skip-permissions`. Include termination conditions in the prompt ("if nothing to do, say 'no changes' and exit"). Save reports to disk and tell the next iteration to read previous commits and reports to find the next improvement.

This is powerful but requires discipline—you need well-defined boundaries and clear stopping conditions. The `/ralph-loop` skill (from compound-engineering) provides structure for this pattern. Don't start here; get the basics solid first.

### Remote Development

Running Claude Code on a remote server lets you detach sessions and reconnect from anywhere—including your phone. Use a dev pod or remote container with tmux, kick off a task, and check in later. This is useful when you're running many parallel agents and don't want them consuming local resources.

### Further Reading

- The plugin READMEs explain each skill in detail
- [sjawhar/dotfiles](https://github.com/sjawhar/dotfiles) shows a complete working setup
- Ask Claude to explain any skill with `/help <skill-name>`
