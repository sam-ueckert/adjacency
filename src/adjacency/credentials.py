"""Credential management with network-range-based pattern matching.

Credentials are loaded from a YAML file and matched to target devices by
IP address.  Each credential entry can optionally be scoped to one or more
CIDR network ranges and/or platform hints.  When multiple entries match,
they are tried in file order (most-specific-network first within a tier).

Credential file format
----------------------
.. code-block:: yaml

    credentials:
      - name: spine-layer
        username: admin
        password: s3cret
        platform: eos          # optional NAPALM driver hint
        networks:              # optional; omit for "match everything"
          - 10.0.0.0/24
          - 10.0.1.0/24

      - name: access-ios
        username: netops
        password: acc3ss!
        secret: enable_pass    # IOS enable secret
        platform: ios
        networks:
          - 10.1.0.0/16

      - name: fallback
        username: admin
        password: admin
        # no networks → fallback, tried last
"""

from __future__ import annotations

import ipaddress
import logging
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)


class Credential(BaseModel):
    """A single credential entry with optional network scope."""

    name: str = ""
    username: str
    password: str = ""
    secret: str = ""  # enable / privilege secret
    platform: str | None = None  # NAPALM driver name hint

    # CIDR ranges this credential applies to.  Empty list = wildcard.
    networks: list[str] = Field(default_factory=list)

    # Parsed at load time — not serialised.
    _parsed_networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []

    def model_post_init(self, _context: Any) -> None:
        nets = []
        for n in self.networks:
            try:
                nets.append(ipaddress.ip_network(n, strict=False))
            except ValueError:
                log.warning("Invalid network '%s' in credential '%s', skipping", n, self.name)
        object.__setattr__(self, "_parsed_networks", nets)

    def matches_ip(self, ip: str) -> bool:
        """True if *ip* falls within any of this credential's network scopes,
        or if no networks are defined (wildcard match)."""
        if not self._parsed_networks:
            return True  # wildcard
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return any(addr in net for net in self._parsed_networks)

    @property
    def is_scoped(self) -> bool:
        """True if this credential is restricted to specific networks."""
        return len(self._parsed_networks) > 0


class CredentialStore(BaseModel):
    """Ordered list of credentials loaded from a YAML file."""

    credentials: list[Credential] = Field(default_factory=list)

    def match(self, ip: str) -> list[Credential]:
        """Return credentials that match *ip*, ordered by specificity.

        Scoped credentials (those with explicit network ranges) come first,
        followed by wildcard entries.  Within each tier, file order is preserved.
        """
        scoped: list[Credential] = []
        wildcards: list[Credential] = []
        for cred in self.credentials:
            if not cred.matches_ip(ip):
                continue
            if cred.is_scoped:
                scoped.append(cred)
            else:
                wildcards.append(cred)
        return scoped + wildcards

    def match_with_platform(self, ip: str, platform_hint: str | None = None) -> list[Credential]:
        """Like ``match()`` but also prioritises credentials whose platform
        matches the given hint (e.g. from LLDP system description).
        """
        candidates = self.match(ip)
        if not platform_hint:
            return candidates

        # Partition into platform-matched and others
        matched: list[Credential] = []
        rest: list[Credential] = []
        for c in candidates:
            if c.platform and c.platform.lower() == platform_hint.lower():
                matched.append(c)
            else:
                rest.append(c)
        return matched + rest


def load_credentials(path: Path) -> CredentialStore:
    """Load a credential YAML file and return a CredentialStore."""
    raw = yaml.safe_load(path.read_text())
    if not raw or "credentials" not in raw:
        raise ValueError(f"Credential file {path} must contain a 'credentials' key")
    return CredentialStore.model_validate(raw)


# ---------------------------------------------------------------------------
# Platform detection from LLDP/CDP system descriptions
# ---------------------------------------------------------------------------

_PLATFORM_HINTS: list[tuple[str, str]] = [
    ("arista eos", "eos"),
    ("arista", "eos"),
    ("cisco nx-os", "nxos_ssh"),
    ("cisco nexus", "nxos_ssh"),
    ("nx-os", "nxos_ssh"),
    ("cisco ios-xr", "iosxr"),
    ("ios-xr", "iosxr"),
    ("cisco ios-xe", "ios"),  # NAPALM ios driver handles IOS-XE
    ("ios-xe", "ios"),
    ("cisco ios", "ios"),
    ("juniper junos", "junos"),
    ("junos", "junos"),
    ("juniper", "junos"),
    ("palo alto", "panos"),
    ("pan-os", "panos"),
    ("nokia sr os", "sros"),
    ("huawei vrp", "huawei_vrp"),
]


def detect_platform(system_description: str | None) -> str | None:
    """Guess the NAPALM driver name from an LLDP/CDP system description.

    Returns None if no match is found.
    """
    if not system_description:
        return None
    desc_lower = system_description.lower()
    for pattern, driver in _PLATFORM_HINTS:
        if pattern in desc_lower:
            return driver
    return None
