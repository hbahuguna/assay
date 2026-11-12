"""SSRF-safe validation for URL targets.

URL targets are resolved and checked before navigation.  Loopback, link-local,
private, multicast, reserved, unspecified, and cloud-metadata destinations are
blocked unless an operator explicitly allowlists them through configuration.
Redirect chains are re-validated at every hop.
"""

from __future__ import annotations

import ipaddress
import json
import os
import socket
from pathlib import Path
from typing import Any, Callable, Iterable, List, Optional, Sequence, Union
from urllib.parse import urlsplit

from .errors import TargetError


BLOCKED_HOSTNAMES = {
    "metadata.google.internal",
    "metadata",
    "kubernetes.default.svc",
}

DEFAULT_BLOCKED_NETWORKS = [
    "0.0.0.0/8",
    "10.0.0.0/8",
    "100.64.0.0/10",
    "127.0.0.0/8",
    "169.254.0.0/16",
    "172.16.0.0/12",
    "192.0.0.0/24",
    "192.0.2.0/24",
    "192.168.0.0/16",
    "198.18.0.0/15",
    "198.51.100.0/24",
    "203.0.113.0/24",
    "224.0.0.0/4",
    "240.0.0.0/4",
    "255.255.255.255/32",
    "::/128",
    "::1/128",
    "64:ff9b::/96",
    "100::/64",
    "2001:db8::/32",
    "fc00::/7",
    "fe80::/10",
    "ff00::/8",
]

Resolver = Callable[[str], Sequence[str]]


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _default_resolver(hostname: str) -> List[str]:
    try:
        results = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise TargetError("Target host could not be resolved.") from exc
    addresses: List[str] = []
    for result in results:
        sockaddr = result[4]
        if sockaddr:
            address = str(sockaddr[0])
            if address not in addresses:
                addresses.append(address)
    if not addresses:
        raise TargetError("Target host could not be resolved.")
    return addresses


def _parse_allowlist(entries: Optional[Iterable[Any]]) -> tuple[List[str], List[ipaddress._BaseNetwork]]:
    hosts: List[str] = []
    networks: List[ipaddress._BaseNetwork] = []
    for entry in entries or ():
        if isinstance(entry, (list, tuple, set)):
            h, n = _parse_allowlist(entry)
            hosts.extend(h)
            networks.extend(n)
            continue
        text = _safe_text(entry)
        if not text:
            continue
        if "/" in text:
            try:
                networks.append(ipaddress.ip_network(text, strict=False))
            except ValueError:
                raise TargetError("Invalid SSRF allowlist CIDR: {}".format(text))
        else:
            hosts.append(text.lower())
    return hosts, networks


def load_allowlist(root: Optional[Union[str, Path]] = None) -> List[str]:
    """Load an allowlist from environment or ``.harness/ssrf-allowlist.json``."""

    entries: List[str] = []
    env = os.environ.get("QH_SSRF_ALLOWLIST", "")
    if env:
        entries.extend(item.strip() for item in env.split(",") if item.strip())
    env_file = os.environ.get("QH_SSRF_ALLOWLIST_FILE", "")
    paths = []
    if env_file:
        paths.append(Path(env_file))
    if root is not None:
        paths.append(Path(root) / ".harness" / "ssrf-allowlist.json")
        paths.append(Path(root) / "ssrf-allowlist.json")
    for path in paths:
        try:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    values = data.get("allow", data.get("allowlist", []))
                    if isinstance(values, (list, tuple)):
                        entries.extend(str(item) for item in values)
                    elif isinstance(values, str):
                        entries.extend(item.strip() for item in values.split(",") if item.strip())
        except (OSError, ValueError, json.JSONDecodeError):
            raise TargetError("SSRF allowlist configuration is unreadable.")
    return entries


def _is_blocked_address(address: str, allow_hosts: Sequence[str], allow_networks: Sequence[ipaddress._BaseNetwork]) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return True
    if any(ip in network for network in allow_networks):
        return False
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return True
    # Cloud metadata is in link-local, but keep an explicit guard.
    if str(ip) == "169.254.169.254":
        return True
    return False


def validate_url(
    url: str,
    *,
    allowlist: Optional[Iterable[Any]] = None,
    resolver: Optional[Resolver] = None,
    root: Optional[Union[str, Path]] = None,
) -> str:
    """Validate a URL target and return it if safe."""

    parsed = urlsplit(str(url or "").strip())
    if parsed.scheme not in ("http", "https"):
        raise TargetError("Target URL must use http or https.")
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise TargetError("Target URL is missing a host.")
    if host in BLOCKED_HOSTNAMES and host not in (str(item).lower() for item in (allowlist or ())):
        raise TargetError("Target host is not allowed.")

    effective_allowlist = list(allowlist or [])
    if not effective_allowlist and root is not None:
        effective_allowlist = load_allowlist(root)
    allow_hosts, allow_networks = _parse_allowlist(effective_allowlist)

    # If the host itself is allowlisted by name, resolution is still required
    # so DNS rebinding cannot bypass the check on a later call.
    host_allowed_by_name = host in allow_hosts

    resolve = resolver or _default_resolver
    try:
        if _is_ip_literal(host):
            addresses = [host]
        else:
            addresses = list(resolve(host))
    except TargetError:
        raise
    except Exception as exc:
        raise TargetError("Target host could not be resolved.") from exc

    if not addresses:
        raise TargetError("Target host could not be resolved.")

    for address in addresses:
        if host_allowed_by_name:
            # Named allowlist entries still cannot point at metadata addresses.
            try:
                ip = ipaddress.ip_address(address)
            except ValueError:
                raise TargetError("Target host resolves to an invalid address.")
            if str(ip) == "169.254.169.254":
                raise TargetError("Target host is not allowed.")
            continue
        if _is_blocked_address(address, allow_hosts, allow_networks):
            raise TargetError("Target host resolves to a blocked address.")

    return str(url).strip()


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def validate_redirect_chain(
    urls: Iterable[str],
    *,
    allowlist: Optional[Iterable[Any]] = None,
    resolver: Optional[Resolver] = None,
    root: Optional[Union[str, Path]] = None,
) -> None:
    for url in urls:
        validate_url(url, allowlist=allowlist, resolver=resolver, root=root)


def is_safe_url(url: str, **kwargs: Any) -> bool:
    try:
        validate_url(url, **kwargs)
        return True
    except TargetError:
        return False


# Compatibility alias for callers that expect a different name.
validate_target_url = validate_url
ssrf_safe = is_safe_url
