"""Output formatters — Rich tables and JSON/YAML export."""

from __future__ import annotations

import json
from typing import TextIO

from rich.console import Console
from rich.table import Table

from adjacency.models import AdjacencyTable, LinkType


def print_device_summary(table: AdjacencyTable, console: Console | None = None) -> None:
    """Print a summary of discovered devices."""
    console = console or Console()
    rt = Table(title="Discovered Devices")
    rt.add_column("Hostname", style="bold cyan")
    rt.add_column("Platform")
    rt.add_column("Vendor / Model")
    rt.add_column("OS Version")
    rt.add_column("Mgmt IP")
    rt.add_column("DNS Name")
    rt.add_column("Intfs", justify="right")

    for hostname in sorted(table.devices):
        dev = table.devices[hostname]
        hw_model = ""
        os_ver = dev.os_version or ""
        if dev.hardware:
            hw_model = dev.hardware.hardware_model or dev.hardware.model or dev.model or ""
            os_ver = dev.hardware.os_version or os_ver
        vendor_model = ""
        vendor = dev.vendor or (dev.hardware.vendor if dev.hardware else None) or ""
        if vendor and hw_model:
            vendor_model = f"{vendor} {hw_model}"
        elif vendor:
            vendor_model = vendor
        elif hw_model:
            vendor_model = hw_model

        dns = dev.dns_names[0] if dev.dns_names else "-"

        rt.add_row(
            hostname,
            dev.platform or "-",
            vendor_model or "-",
            os_ver or "-",
            dev.management_ip or "-",
            dns,
            str(len(dev.interfaces)),
        )
    console.print(rt)


def print_adjacency(table: AdjacencyTable, console: Console | None = None) -> None:
    """Print the rationalized adjacency links."""
    console = console or Console()
    rt = Table(title="Adjacency Links")
    rt.add_column("Local Device", style="bold cyan")
    rt.add_column("Local Interface")
    rt.add_column("Remote Device", style="bold green")
    rt.add_column("Remote Interface")
    rt.add_column("Type")
    rt.add_column("Sources")
    rt.add_column("Members", justify="right")

    for link in sorted(table.links, key=lambda l: (l.local_device, l.local_interface)):
        member_count = str(len(link.members)) if link.members else "-"
        sources = ", ".join(s.value for s in link.sources)
        type_style = "bold yellow" if link.link_type == LinkType.LAG else ""

        rt.add_row(
            link.local_device,
            link.local_interface,
            link.remote_device,
            link.remote_interface or "-",
            f"[{type_style}]{link.link_type.value}[/]" if type_style else link.link_type.value,
            sources,
            member_count,
        )

        # Show LAG members indented
        if link.members:
            for member in link.members:
                m_sources = ", ".join(s.value for s in member.sources)
                rt.add_row(
                    "",
                    f"  {member.local_interface}",
                    "",
                    f"  {member.remote_interface or '-'}",
                    "  member",
                    m_sources,
                    "-",
                    style="dim",
                )

    console.print(rt)


def print_raw_records(table: AdjacencyTable, console: Console | None = None) -> None:
    """Print all raw neighbor records before rationalization."""
    console = console or Console()
    rt = Table(title="Raw Neighbor Records")
    rt.add_column("Source", style="bold")
    rt.add_column("Local Device")
    rt.add_column("Local Interface")
    rt.add_column("Remote Device")
    rt.add_column("Remote Interface")
    rt.add_column("Remote MAC")
    rt.add_column("Remote IP")

    for rec in sorted(table.raw_records, key=lambda r: (r.local_device, r.source.value)):
        rt.add_row(
            rec.source.value,
            rec.local_device,
            rec.local_interface,
            rec.remote_device or "-",
            rec.remote_interface or "-",
            rec.remote_mac or "-",
            rec.remote_ip or "-",
        )
    console.print(rt)


def export_json(table: AdjacencyTable, fp: TextIO) -> None:
    """Write the adjacency table as JSON."""
    data = table.model_dump(mode="json")
    json.dump(data, fp, indent=2)
    fp.write("\n")
