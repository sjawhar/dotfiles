# Setting direction and coaching the loop

## The stream's three lines

Open the stream's issue with three lines above its targets: the problem the stream exists to solve, the one approach it commits to, and what it will not do. Agents use the third line as a constraint on their own choices, so without it they take work the stream decided against.

Ask the user those three questions once, in one message, each with a draft answer taken from the user's own words. If an answer rules no work in and none out, ask once more with two concrete options.

## Coaching an agent's loop

- An experiment on a number, with a benchmark that runs in minutes, goes through `/autoresearch`: the expected gain is in its notes before each run, and the change is run again before it is kept, so a result that came from noise does not survive.
- Use `skill://ce-optimize` instead when the result is noisy or has several objectives.
- A one-off change still gets its prediction written in the scoreboard before it starts, so a wrong prediction is visible instead of explained away afterwards.
