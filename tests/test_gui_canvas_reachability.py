#!/usr/bin/env python3
"""Canvas HTTP base fallback for GUI clients when server hostname does not resolve."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import sshchat_gui as gui  # noqa: E402


def test_fallback_to_ssh_host_and_local_port() -> None:
    url = "https://unresolvable.internal/canvas/abc123"
    bases = gui._http_base_candidates(url, "192.168.0.202")
    assert bases[0] == "https://unresolvable.internal"
    assert any("192.168.0.202" in b for b in bases)
    assert "http://192.168.0.202:8443" in bases


def test_lan_ip_url_keeps_original_first() -> None:
    url = "https://192.168.0.202:8443/canvas/tok"
    bases = gui._http_base_candidates(url, "192.168.0.202")
    assert bases[0] == "https://192.168.0.202:8443"


def test_lan_ip_without_port_adds_8443() -> None:
    url = "https://192.168.0.202/piano/tok"
    bases = gui._http_base_candidates(url, "192.168.0.202")
    assert bases[0] == "https://192.168.0.202"
    assert "http://192.168.0.202:8443" in bases
    assert "https://192.168.0.202:8443" in bases


if __name__ == "__main__":
    test_fallback_to_ssh_host_and_local_port()
    test_lan_ip_url_keeps_original_first()
    test_lan_ip_without_port_adds_8443()
    print("✅ gui canvas reachability ok")
