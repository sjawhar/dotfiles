"""Chart style and helpers for a standup recap page.

Copy this file next to the recap, add one block per chart with its numbers and a comment naming the source, and
run it with `uv run --with matplotlib python chart_style.py`. It writes SVG and PNG into `site/img/`.

Rules the style enforces: body-size type, a shaded weekend band, day axis labels a reader can scan, whole-number
axes for counts, and text that stays inside the figure.
"""
import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import matplotlib.ticker
from datetime import datetime as dt, timedelta
from pathlib import Path

OUT = Path(__file__).parent / "site" / "img"
OUT.mkdir(parents=True, exist_ok=True)

INK, MUTED, ACCENT, GOOD, WEEKEND = "#1f2937", "#6b7280", "#2563eb", "#16a34a", "#fef3c7"
plt.rcParams.update({
    "font.size": 15, "axes.titlesize": 17, "axes.titleweight": "bold", "axes.edgecolor": MUTED,
    "axes.labelcolor": INK, "xtick.color": INK, "ytick.color": INK, "axes.spines.top": False,
    "axes.spines.right": False, "svg.fonttype": "none", "font.family": "DejaVu Sans",
})

# Set these to THIS recap's window before you draw anything: the weekend band and the timeline read them, and a
# stale value shades the wrong days.
WEEKEND_START, WEEKEND_END = "2026-10-31", "2026-11-02"  # Saturday 00:00 to Monday 00:00, Pacific


def d(s: str) -> dt:
    return dt.strptime(s, "%Y-%m-%d")


def weekend(ax) -> None:
    """Shade the weekend and label it, so a weekend delivery is visible without reading the text."""
    ax.axvspan(d(WEEKEND_START), d(WEEKEND_END), color=WEEKEND, zorder=0)
    ax.text(d(WEEKEND_START) + (d(WEEKEND_END) - d(WEEKEND_START)) / 2, ax.get_ylim()[1] * 0.97, "weekend",
            ha="center", va="top", color="#92400e", fontsize=13)


def days_axis(ax, first: str, last: str) -> None:
    ax.xaxis.set_major_locator(mdates.DayLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%a\n%-m/%-d"))
    ax.set_xlim(d(first), d(last))


def whole_numbers(ax) -> None:
    """A count of tasks or releases has no half values."""
    ax.yaxis.set_major_locator(matplotlib.ticker.MaxNLocator(integer=True))


def save(fig, name: str) -> None:
    fig.tight_layout()
    fig.savefig(OUT / f"{name}.svg", format="svg")
    fig.savefig(OUT / f"{name}.png", format="png", dpi=110)
    plt.close(fig)


def timeline(lanes: list[str], events: list[tuple[int, str, str]], first: str, last: str,
             title: str = "What landed, day by day") -> None:
    """One row per project, one dot per delivery. `events` are (lane index, YYYY-MM-DD, two-line label).

    Labels sit above their dot and are placed at midday, so two dots on neighbouring days do not overlap.
    """
    fig, ax = plt.subplots(figsize=(13, 6.2))
    ax.axvspan(d(WEEKEND_START), d(WEEKEND_END), color=WEEKEND, zorder=0)
    ax.text(d(WEEKEND_START) + (d(WEEKEND_END) - d(WEEKEND_START)) / 2, -0.8, "weekend", ha="center", va="center",
            color="#92400e", fontsize=13)
    colors = ["#2563eb", "#7c3aed", "#0891b2", "#dc2626", "#16a34a", "#ca8a04"]
    for lane, day, label in events:
        x = d(day) + timedelta(hours=12)
        y = len(lanes) - 1 - lane
        ax.plot([x], [y], "o", color=colors[lane % len(colors)], ms=12, zorder=3)
        ax.text(x, y + 0.16, label, ha="center", va="bottom", fontsize=11.5, color=INK, zorder=4, linespacing=1.1)
    ax.set_yticks(range(len(lanes)))
    ax.set_yticklabels(list(reversed(lanes)), fontsize=14, fontweight="bold")
    ax.set_ylim(-1.1, len(lanes) - 0.25)
    ax.xaxis.set_major_locator(mdates.HourLocator(byhour=[12]))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%a %-m/%-d"))
    ax.set_xlim(d(first), d(last))
    ax.grid(axis="y", color="#e5e7eb")
    ax.spines["left"].set_visible(False)
    ax.set_title(title)
    save(fig, "week")


def _example() -> None:
    """Shape only. Delete it and write this recap's charts, each with its source in a comment."""
    timeline(
        ["OSS Verify", "Tasks", "Grading"],
        [(0, "2026-10-28", "release\n138→202 proved"), (1, "2026-11-01", "11 meet\nthe bar"),
         (2, "2026-10-27", "25,635 of 25,637\nanswers agree")],
        first="2026-10-26", last="2026-11-03",
    )


if __name__ == "__main__":
    _example()
    print(sorted(p.name for p in OUT.iterdir()))
