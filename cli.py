"""Terminal interface: renders with rich the comparison produced by core."""

import argparse
import sys

from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

import core

console = Console(highlight=False)

COLORS = {"+": "green", "-": "red", "~": "yellow"}
EMPTY = "[dim]—[/dim]"


def header(report: dict) -> Panel:
    size, layers = report["size_mb"], report["layers"]
    delta = size["delta"]
    if abs(delta) < 0.05:
        delta_text, delta_color = "same size", "dim"
    else:
        delta_text = f"{delta:+.1f} MB"
        delta_color = "green" if delta < 0 else "red"

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold dim")
    grid.add_column(style="bold")
    grid.add_column(justify="right")
    grid.add_column(justify="right", style="dim")
    grid.add_row("A", report["a"], f"{size['a']:.1f} MB", f"{layers['a']} layers")
    grid.add_row("B", report["b"], f"{size['b']:.1f} MB", f"{layers['b']} layers")
    grid.add_row("", "", Text(delta_text, style=f"bold {delta_color}"), "")

    legend = Text.assemble(
        ("+", "green"), (" added   ", "dim"),
        ("-", "red"), (" removed   ", "dim"),
        ("~", "yellow"), (" changed", "dim"),
    )
    return Panel(Group(grid, "", legend), box=box.ROUNDED,
                 border_style="dim", expand=False)


def table(changes: list[core.Change]) -> Table:
    t = Table(box=box.SIMPLE_HEAD, expand=False, pad_edge=False, show_edge=False)
    t.add_column(" ", width=1)
    t.add_column("name", style="bold", no_wrap=True)
    t.add_column("before", style="dim", overflow="fold")
    t.add_column("after", overflow="fold")
    for c in changes:
        color = COLORS[c.kind]
        t.add_row(
            Text(c.kind, style=f"bold {color}"),
            Text(c.name, style=color),
            c.before if c.before is not None else EMPTY,
            c.after if c.after is not None else EMPTY,
        )
    return t


def section(title: str, changes: list | None, reason: str = "") -> None:
    console.print(f"\n[bold]{title}[/bold]", end="")
    if changes is None:
        console.print(f"  [dim]({reason})[/dim]")
    elif not changes:
        console.print("  [dim](no changes)[/dim]")
    else:
        console.print(f"  [dim]{len(changes)} change(s)[/dim]")
        console.print(table(changes))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="imgdiff",
        description="compare two Docker images and show what changed",
        epilog="example: python cli.py nginx:1.26.0 nginx:1.27.0",
    )
    p.add_argument("image_a", help="e.g. nginx:1.26.0")
    p.add_argument("image_b", help="e.g. nginx:1.27.0")
    p.add_argument("--fast", action="store_true",
                   help="skip the package diff, which downloads image layers")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        with console.status("[dim]fetching manifests...[/dim]"):
            img_a = core.fetch_image(args.image_a)
            img_b = core.fetch_image(args.image_b)
            report = core.compare(img_a, img_b)

        console.print()
        console.print(header(report))
        section("ENV", report["env"])

        if args.fast:
            section("PACKAGES", None, "skipped by --fast")
        else:
            with console.status("[dim]downloading layers to read the package list...[/dim]"):
                pkgs_a = core.read_packages(img_a)
                pkgs_b = core.read_packages(img_b)
            if pkgs_a is None or pkgs_b is None:
                section("PACKAGES", None, "not a Debian/Ubuntu image")
            else:
                section("PACKAGES", core.diff_dicts(pkgs_a, pkgs_b))

        console.print()
    except core.ImgdiffError as e:
        console.print(Panel(str(e), title="error", border_style="red", box=box.ROUNDED))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
