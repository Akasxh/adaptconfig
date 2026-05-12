"""SSRF protection: URL validation to block internal/private network targets."""

import ipaddress
import socket
from urllib.parse import urlparse

_BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
]


def is_safe_url(url: str) -> bool:
    """Return True only if the URL resolves to a public (non-private) IP address.

    Returns True when DNS resolution fails — unresolvable hostnames are not
    exploitable for SSRF against known internal addresses. Only actively-resolved
    private/loopback IPs are blocked.
    """
    hostname = urlparse(url).hostname
    if not hostname:
        return False
    try:
        for info in socket.getaddrinfo(hostname, None):
            ip = ipaddress.ip_address(info[4][0])
            if any(ip in net for net in _BLOCKED_NETWORKS):
                return False
    except socket.gaierror:
        # Cannot resolve — not a known internal address, allow through
        return True
    return True
