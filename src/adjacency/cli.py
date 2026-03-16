"""CLI interface for adjacency discovery."""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from adjacency import __version__


DEFAULT_INVENTORY = Path("inventory")

console = Console()


@click.group()
@click.version_option(version=__version__)
@click.option(
    "--inventory", "-i",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Path to Nornir inventory directory (inventory mode).",
)
@click.option(
    "--snapshot-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Override snapshot storage directory.",
)
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose/debug logging.")
@click.pass_context
def main(ctx: click.Context, inventory: Path | None, snapshot_dir: Path | None, verbose: bool) -> None:
    """adjacency -- intelligent network device adjacency mapping."""
    ctx.ensure_object(dict)
    ctx.obj["inventory"] = inventory
    ctx.obj["snapshot_dir"] = snapshot_dir
    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")
    else:
        logging.basicConfig(level=logging.WARNING)


# ---------------------------------------------------------------------------
# discover
# ---------------------------------------------------------------------------

@main.command()
# -- crawl mode options --
@click.option("--seed", "-s", "seeds", multiple=True,
              help="Seed device (IP or host[:platform]).  Repeatable.  Activates crawl mode.")
@click.option("--credentials", "-c",
              type=click.Path(exists=True, dir_okay=False, path_type=Path), default=None,
              help="Credential YAML file for crawl mode.")
@click.option("--depth", "-d", type=int, default=1,
              help="Crawl depth: 0=seeds only, 1=seeds+neighbors, etc.  [default: 1]")
@click.option("--timeout", type=int, default=30,
              help="Per-device connection timeout in seconds.  [default: 30]")
@click.option("--max-workers", type=int, default=10,
              help="Parallel device connections during crawl.  [default: 10]")
# -- shared options --
@click.option("--no-l2", is_flag=True, help="Skip MAC table collection.")
@click.option("--no-l3", is_flag=True, help="Skip ARP table collection.")
@click.option("--no-cdp", is_flag=True, help="Skip CDP collection.")
@click.option("--no-routes", is_flag=True, help="Skip route table collection.")
@click.option("--no-facts", is_flag=True, help="Skip hardware facts collection.")
@click.option("--no-rdns", is_flag=True, help="Skip reverse DNS lookups.")
@click.option("--no-save", is_flag=True, help="Do not auto-save a snapshot.")
@click.option("--label", "-l", default="", help="Label for the saved snapshot.")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON instead of tables.")
@click.option("--raw", is_flag=True, help="Show raw records before rationalization.")
@click.pass_context
def discover(
    ctx: click.Context,
    seeds: tuple[str, ...],
    credentials: Path | None,
    depth: int,
    timeout: int,
    max_workers: int,
    no_l2: bool,
    no_l3: bool,
    no_cdp: bool,
    no_routes: bool,
    no_facts: bool,
    no_rdns: bool,
    no_save: bool,
    label: str,
    as_json: bool,
    raw: bool,
) -> None:
    """Discover adjacencies from network devices.

    Two modes:

    \b
    INVENTORY MODE (default):
      Uses a Nornir inventory directory (hosts.yaml + defaults.yaml).
      adjacency -i inventory/ discover

    \b
    CRAWL MODE:
      Starts from seed devices and crawls outward to --depth hops.
      adjacency discover --seed 10.0.0.1 --seed 10.0.0.2:eos -d 2 -c creds.yaml
    """
    from adjacency.output import (
        export_json,
        print_adjacency,
        print_device_summary,
        print_raw_records,
    )
    from adjacency.store import save_snapshot

    snapshot_dir = ctx.obj["snapshot_dir"]

    if seeds:
        # ---- Crawl mode ----
        table = _run_crawl(
            seeds, credentials, depth, timeout, max_workers,
            not no_l2, not no_l3, not no_cdp, not no_routes, not no_rdns,
        )
    else:
        # ---- Inventory mode ----
        inventory = ctx.obj["inventory"]
        if inventory is None:
            # Try default
            if DEFAULT_INVENTORY.exists():
                inventory = DEFAULT_INVENTORY
            else:
                console.print(
                    "[red]No --seed specified and no inventory directory found.[/]\n"
                    "Use --seed for crawl mode or -i for inventory mode."
                )
                raise SystemExit(1)

        table = _run_inventory(
            inventory,
            not no_l2, not no_l3, not no_cdp, not no_routes, not no_facts, not no_rdns,
        )

    # ---- Output ----
    if as_json:
        export_json(table, sys.stdout)
    else:
        print_device_summary(table, console)
        console.print()
        print_adjacency(table, console)

        if raw:
            console.print()
            print_raw_records(table, console)

        console.print(
            f"\n[bold green]{len(table.devices)}[/] devices, "
            f"[bold green]{len(table.links)}[/] links "
            f"(from [bold]{len(table.raw_records)}[/] raw records)"
        )

    # Auto-save snapshot
    if not no_save:
        inv_path = ctx.obj.get("inventory")
        path = save_snapshot(table, inv_path, label=label, snapshot_dir=snapshot_dir)
        console.print(f"[dim]Snapshot saved:[/] {path}")


def _run_inventory(
    inventory: Path,
    collect_l2: bool,
    collect_l3: bool,
    collect_cdp: bool,
    collect_routes: bool,
    collect_hw_facts: bool,
    do_rdns: bool,
):
    """Inventory-mode discovery using Nornir."""
    from adjacency.collector import discover as inv_discover

    console.print(f"[bold]Discovering adjacencies from inventory[/] {inventory} ...")
    return asyncio.run(inv_discover(
        inventory,
        collect_l2=collect_l2,
        collect_l3=collect_l3,
        collect_cdp=collect_cdp,
        collect_route_table=collect_routes,
        collect_hw_facts=collect_hw_facts,
        do_rdns=do_rdns,
    ))


def _run_crawl(
    seeds_raw: tuple[str, ...],
    credentials_path: Path | None,
    depth: int,
    timeout: int,
    max_workers: int,
    collect_l2: bool,
    collect_l3: bool,
    collect_cdp: bool,
    collect_routes: bool,
    do_rdns: bool,
):
    """Crawl-mode discovery starting from seed devices."""
    from adjacency.crawl import SeedDevice, crawl
    from adjacency.credentials import CredentialStore, load_credentials

    # Parse seeds: "10.0.0.1" or "10.0.0.1:eos"
    parsed_seeds: list[SeedDevice] = []
    for s in seeds_raw:
        if ":" in s and not s.startswith("["):
            # Could be host:platform or IPv6.  Simple heuristic:
            # if splitting gives exactly 2 parts and second is short, it's platform.
            parts = s.rsplit(":", 1)
            if len(parts[1]) <= 12 and not parts[1].replace(".", "").isdigit():
                parsed_seeds.append(SeedDevice(host=parts[0], platform=parts[1]))
            else:
                parsed_seeds.append(SeedDevice(host=s))
        else:
            parsed_seeds.append(SeedDevice(host=s))

    # Load credentials
    if credentials_path:
        cred_store = load_credentials(credentials_path)
    else:
        console.print("[yellow]No --credentials file specified; crawl may fail to authenticate.[/]")
        cred_store = CredentialStore()

    console.print(
        f"[bold]Crawling from {len(parsed_seeds)} seed(s)[/], "
        f"depth={depth}, {len(cred_store.credentials)} credential(s)"
    )
    for seed in parsed_seeds:
        plat = f" ({seed.platform})" if seed.platform else ""
        console.print(f"  [cyan]{seed.host}[/]{plat}")

    return asyncio.run(crawl(
        parsed_seeds,
        cred_store,
        max_depth=depth,
        max_workers=max_workers,
        collect_l2=collect_l2,
        collect_l3=collect_l3,
        collect_cdp=collect_cdp,
        collect_routes=collect_routes,
        do_rdns=do_rdns,
        timeout=timeout,
    ))


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------

@main.command()
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--raw", is_flag=True, help="Show raw records.")
def show(file: Path, raw: bool) -> None:
    """Display a previously saved adjacency JSON file."""
    import json as json_mod

    from adjacency.models import AdjacencyTable
    from adjacency.output import print_adjacency, print_device_summary, print_raw_records

    data = json_mod.loads(file.read_text())
    # Support both bare AdjacencyTable and SnapshotEnvelope format
    if "data" in data and "meta" in data:
        data = data["data"]
    table = AdjacencyTable.model_validate(data)

    print_device_summary(table, console)
    console.print()
    print_adjacency(table, console)

    if raw:
        console.print()
        print_raw_records(table, console)


# ---------------------------------------------------------------------------
# snapshot subgroup
# ---------------------------------------------------------------------------

@main.group()
def snapshot() -> None:
    """Manage saved adjacency snapshots."""


@snapshot.command("list")
@click.pass_context
def snapshot_list(ctx: click.Context) -> None:
    """List all saved snapshots."""
    from adjacency.store import list_snapshots

    snapshot_dir = ctx.obj["snapshot_dir"]
    metas = list_snapshots(snapshot_dir)

    if not metas:
        console.print("[dim]No snapshots found.[/]")
        return

    rt = Table(title="Saved Snapshots")
    rt.add_column("ID", style="bold cyan")
    rt.add_column("Created")
    rt.add_column("Label")
    rt.add_column("Devices", justify="right")
    rt.add_column("Links", justify="right")
    rt.add_column("Records", justify="right")

    for m in metas:
        ts = m.created_at[:19].replace("T", " ")
        rt.add_row(
            m.snapshot_id,
            ts,
            m.label or "-",
            str(m.device_count),
            str(m.link_count),
            str(m.raw_record_count),
        )
    console.print(rt)


@snapshot.command("load")
@click.argument("identifier")
@click.option("--raw", is_flag=True, help="Show raw records.")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
@click.pass_context
def snapshot_load(ctx: click.Context, identifier: str, raw: bool, as_json: bool) -> None:
    """Load and display a snapshot by ID or label."""
    from adjacency.output import (
        export_json,
        print_adjacency,
        print_device_summary,
        print_raw_records,
    )
    from adjacency.store import load_snapshot

    snapshot_dir = ctx.obj["snapshot_dir"]
    try:
        meta, table = load_snapshot(identifier, snapshot_dir)
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/]")
        raise SystemExit(1)

    console.print(f"[dim]Snapshot:[/] {meta.snapshot_id}  [dim]Created:[/] {meta.created_at}")
    if meta.label:
        console.print(f"[dim]Label:[/] {meta.label}")
    console.print()

    if as_json:
        export_json(table, sys.stdout)
        return

    print_device_summary(table, console)
    console.print()
    print_adjacency(table, console)

    if raw:
        console.print()
        print_raw_records(table, console)


@snapshot.command("delete")
@click.argument("identifier")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt.")
@click.pass_context
def snapshot_delete(ctx: click.Context, identifier: str, yes: bool) -> None:
    """Delete a snapshot by ID or label."""
    from adjacency.store import delete_snapshot

    snapshot_dir = ctx.obj["snapshot_dir"]
    if not yes:
        click.confirm(f"Delete snapshot '{identifier}'?", abort=True)

    if delete_snapshot(identifier, snapshot_dir):
        console.print(f"[green]Deleted.[/]")
    else:
        console.print(f"[red]No snapshot matching '{identifier}' found.[/]")
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# visualize
# ---------------------------------------------------------------------------

@main.command()
@click.argument("source", required=False)
@click.option("--format", "-f", "fmt", type=click.Choice(["html", "dot"]), default="html",
              help="Output format.")
@click.option("--output", "-o", type=click.Path(path_type=Path), default=None,
              help="Output file path.")
@click.option("--title", "-t", default="Adjacency Map", help="Title for HTML output.")
@click.pass_context
def visualize(ctx: click.Context, source: str | None, fmt: str, output: Path | None, title: str) -> None:
    """Generate a topology visualization from a snapshot or JSON file.

    SOURCE can be a snapshot ID/label, a JSON file path, or omitted to use
    the most recent snapshot.
    """
    import json as json_mod

    from adjacency.models import AdjacencyTable
    from adjacency.store import list_snapshots, load_snapshot
    from adjacency.visualize import generate_dot, generate_html

    snapshot_dir = ctx.obj["snapshot_dir"]
    table: AdjacencyTable

    if source and Path(source).exists():
        # Source is a file path
        data = json_mod.loads(Path(source).read_text())
        if "data" in data and "meta" in data:
            data = data["data"]
        table = AdjacencyTable.model_validate(data)
    elif source:
        # Source is a snapshot ID/label
        try:
            _, table = load_snapshot(source, snapshot_dir)
        except FileNotFoundError as e:
            console.print(f"[red]{e}[/]")
            raise SystemExit(1)
    else:
        # Use most recent snapshot
        metas = list_snapshots(snapshot_dir)
        if not metas:
            console.print("[red]No snapshots found. Run 'adjacency discover' first.[/]")
            raise SystemExit(1)
        _, table = load_snapshot(metas[0].snapshot_id, snapshot_dir)
        console.print(f"[dim]Using latest snapshot:[/] {metas[0].snapshot_id}")

    # Default output path
    if not output:
        ts = datetime.now().strftime("%Y%m%d")
        output = Path(f"adjacency_{ts}.{fmt}")

    if fmt == "html":
        path = generate_html(table, output, title=title)
        console.print(f"[green]HTML map written to:[/] {path}")
        console.print(f"[dim]Open in browser:[/] open {path}")
    else:
        dot_text = generate_dot(table, output)
        console.print(f"[green]DOT file written to:[/] {output}")
        console.print(f"[dim]Render with:[/] neato -Tpng {output} -o adjacency.png")


def main_entry() -> None:
    main()


if __name__ == "__main__":
    main_entry()
