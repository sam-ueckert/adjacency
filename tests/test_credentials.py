"""Tests for credential management and platform detection."""

from pathlib import Path

import yaml

from adjacency.credentials import (
    Credential,
    CredentialStore,
    detect_platform,
    load_credentials,
)


class TestCredentialMatching:
    def test_wildcard_matches_anything(self):
        cred = Credential(username="admin", password="pass")
        assert cred.matches_ip("10.0.0.1")
        assert cred.matches_ip("192.168.1.1")
        assert not cred.is_scoped

    def test_scoped_matches_within_range(self):
        cred = Credential(username="admin", password="pass", networks=["10.0.0.0/24"])
        assert cred.matches_ip("10.0.0.1")
        assert cred.matches_ip("10.0.0.254")
        assert not cred.matches_ip("10.0.1.1")
        assert cred.is_scoped

    def test_multiple_networks(self):
        cred = Credential(
            username="admin", password="pass",
            networks=["10.0.0.0/24", "192.168.1.0/24"],
        )
        assert cred.matches_ip("10.0.0.5")
        assert cred.matches_ip("192.168.1.100")
        assert not cred.matches_ip("172.16.0.1")

    def test_invalid_ip_returns_false(self):
        cred = Credential(username="admin", password="pass", networks=["10.0.0.0/24"])
        assert not cred.matches_ip("not-an-ip")


class TestCredentialStore:
    def _store(self) -> CredentialStore:
        return CredentialStore(credentials=[
            Credential(name="spine", username="admin", password="s1",
                       platform="eos", networks=["10.0.0.0/24"]),
            Credential(name="leaf", username="admin", password="s2",
                       platform="nxos_ssh", networks=["10.0.1.0/24"]),
            Credential(name="fallback", username="admin", password="fb"),
        ])

    def test_match_scoped_first(self):
        store = self._store()
        matches = store.match("10.0.0.5")
        assert len(matches) == 2  # spine + fallback
        assert matches[0].name == "spine"
        assert matches[1].name == "fallback"

    def test_match_fallback_only(self):
        store = self._store()
        matches = store.match("172.16.0.1")
        assert len(matches) == 1
        assert matches[0].name == "fallback"

    def test_match_with_platform_prioritizes(self):
        store = self._store()
        # 10.0.0.5 matches spine(eos) + fallback.  With platform_hint=eos,
        # spine should still be first.
        matches = store.match_with_platform("10.0.0.5", "eos")
        assert matches[0].name == "spine"

    def test_match_with_platform_reorders(self):
        store = CredentialStore(credentials=[
            Credential(name="a", username="u", password="p", platform="ios"),
            Credential(name="b", username="u", password="p", platform="eos"),
        ])
        # With hint "eos", b should come first
        matches = store.match_with_platform("10.0.0.1", "eos")
        assert matches[0].name == "b"
        assert matches[1].name == "a"

    def test_empty_store(self):
        store = CredentialStore()
        assert store.match("10.0.0.1") == []


class TestLoadCredentials:
    def test_load_from_file(self, tmp_path):
        data = {
            "credentials": [
                {"name": "test", "username": "admin", "password": "pass",
                 "networks": ["10.0.0.0/8"]},
            ],
        }
        f = tmp_path / "creds.yaml"
        f.write_text(yaml.dump(data))
        store = load_credentials(f)
        assert len(store.credentials) == 1
        assert store.credentials[0].name == "test"
        assert store.credentials[0].matches_ip("10.1.2.3")

    def test_load_with_secret(self, tmp_path):
        data = {
            "credentials": [
                {"username": "admin", "password": "pass", "secret": "enable123"},
            ],
        }
        f = tmp_path / "creds.yaml"
        f.write_text(yaml.dump(data))
        store = load_credentials(f)
        assert store.credentials[0].secret == "enable123"


class TestPlatformDetection:
    def test_arista_eos(self):
        assert detect_platform("Arista Networks EOS version 4.28.0F") == "eos"

    def test_cisco_nxos(self):
        assert detect_platform("Cisco NX-OS(tm) n9000") == "nxos_ssh"

    def test_cisco_nexus(self):
        assert detect_platform("Cisco Nexus Operating System") == "nxos_ssh"

    def test_cisco_ios(self):
        assert detect_platform("Cisco IOS Software, C3750E") == "ios"

    def test_cisco_iosxr(self):
        assert detect_platform("Cisco IOS-XR Software, Version 6.5.3") == "iosxr"

    def test_cisco_iosxe(self):
        assert detect_platform("Cisco IOS-XE Software, Catalyst 9300") == "ios"

    def test_juniper(self):
        assert detect_platform("Juniper Networks, Inc. ex4300-48t") == "junos"

    def test_unknown(self):
        assert detect_platform("SomeUnknownVendor OS v1.0") is None

    def test_none_input(self):
        assert detect_platform(None) is None

    def test_case_insensitive(self):
        assert detect_platform("ARISTA EOS") == "eos"
