# The page

One page, read top to bottom in five minutes while it is on a shared screen.

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

A weekly page holds five or six projects; a daily standup holds the same shape with one item per project and no
charts, as a message rather than a page.

## Charts

- The timeline across the top: one row per project, one dot per delivery, the weekend shaded. It is the only
  picture most readers will study.
- At most one chart per project, each proving one item: a count over the window, a before and after, or a
  release-by-release bar.
- `scripts/chart_style.py` holds the style (type sizes, the weekend band, day axis, SVG and PNG output). Write the
  numbers in the chart script with their source in a comment, so the page can be rebuilt from it.
- Look at every chart as an image before the page goes out.

## Serving it

Build with `scripts/build_page.py` (markdown to one self-contained HTML file) and serve the directory on the
tailnet: `sudo tailscale serve --bg --https=<free port> <site dir>`. Check `tailscale serve status` for a free
port, and take a full-page screenshot through `browser.open` before you send the link.
