# The page

One page, read top to bottom in five minutes on a shared screen.

## Shape

```markdown
# What we delivered: <Mon D> – <Mon D>

Sami's agent teams. Times are Pacific. Items done on Saturday or Sunday are marked **weekend**.

![The week day by day](img/week.svg)

## The week in brief
<One line per project, in priority order, each the project's single best result with its number.
Then one line, "Behind:", naming every goal that will be missed, with its date and where we are.>

## <Project>
<A one-sentence definition of the one term the section cannot avoid, when it has one.>
<2 to 4 items. Each starts with the result in bold, then its number with its comparison, then its caveat.>
<Charts beside the items they prove.>

## This week
<One line per project: the next delivery, not an intention.>
```

A weekly page holds five or six projects. A daily standup or chat recap keeps this shape with one item per project
and no charts, as a message rather than a page.

## Charts

- The timeline across the top: one row per project, one dot per delivery, the weekend shaded. Most readers study
  only this picture.
- At most one more chart per project, each proving one item: a count over the window, a before and after, or a
  release-by-release bar.
- Make the charts with `scripts/chart_style.py` (its docstring says how). Write each chart's numbers in the script
  with a comment naming their source, so the page can be rebuilt from it.

## Building and serving

1. Build with `uv run --with markdown python <skill dir>/scripts/build_page.py recap.md`.
2. Pick a free port from `tailscale serve status`, then serve the site directory on the tailnet:
   `sudo tailscale serve --bg --https=<port> <site dir>`.
3. Before you send the link, open the page with `browser.open` and take a full-page screenshot
   (`tab.screenshot({fullPage: true})`).
