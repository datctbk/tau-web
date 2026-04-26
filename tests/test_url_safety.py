"""Tests for tau-web URL safety / SSRF blocking."""

import socket
from unittest.mock import patch

import pytest

# Import from tau-web extension path
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "extensions", "web"))
from url_safety import is_safe_url, _is_blocked_ip

import ipaddress


class TestIsBlockedIp:
    def test_loopback(self):
        assert _is_blocked_ip(ipaddress.ip_address("127.0.0.1"))

    def test_private_10(self):
        assert _is_blocked_ip(ipaddress.ip_address("10.0.0.1"))

    def test_private_172(self):
        assert _is_blocked_ip(ipaddress.ip_address("172.16.0.1"))

    def test_private_192(self):
        assert _is_blocked_ip(ipaddress.ip_address("192.168.1.1"))

    def test_link_local(self):
        assert _is_blocked_ip(ipaddress.ip_address("169.254.169.254"))

    def test_cgnat(self):
        assert _is_blocked_ip(ipaddress.ip_address("100.64.0.1"))

    def test_public_ip_not_blocked(self):
        assert not _is_blocked_ip(ipaddress.ip_address("8.8.8.8"))
        assert not _is_blocked_ip(ipaddress.ip_address("1.1.1.1"))

    def test_multicast(self):
        assert _is_blocked_ip(ipaddress.ip_address("224.0.0.1"))

    def test_ipv6_loopback(self):
        assert _is_blocked_ip(ipaddress.ip_address("::1"))

    def test_ipv6_link_local(self):
        assert _is_blocked_ip(ipaddress.ip_address("fe80::1"))


class TestIsSafeUrl:
    def test_empty_url(self):
        assert not is_safe_url("")

    def test_no_hostname(self):
        assert not is_safe_url("file:///etc/passwd")

    def test_blocked_hostname_metadata(self):
        assert not is_safe_url("http://metadata.google.internal/computeMetadata/v1/")

    def test_blocked_hostname_metadata_goog(self):
        assert not is_safe_url("http://metadata.goog/computeMetadata/v1/")

    @patch("socket.getaddrinfo")
    def test_public_ip_allowed(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0)),
        ]
        assert is_safe_url("https://example.com")

    @patch("socket.getaddrinfo")
    def test_localhost_blocked(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 0)),
        ]
        assert not is_safe_url("http://localhost:8080")

    @patch("socket.getaddrinfo")
    def test_private_ip_blocked(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.0.0.5", 0)),
        ]
        assert not is_safe_url("http://internal-server.corp/api")

    @patch("socket.getaddrinfo", side_effect=socket.gaierror("DNS failed"))
    def test_dns_failure_blocked(self, mock_getaddrinfo):
        # DNS resolution failure → fail closed
        assert not is_safe_url("http://nonexistent.example.invalid")

    @patch("socket.getaddrinfo")
    def test_cloud_metadata_ip_blocked(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("169.254.169.254", 0)),
        ]
        assert not is_safe_url("http://evil.com")

    @patch("socket.getaddrinfo")
    def test_cgnat_blocked(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("100.100.100.1", 0)),
        ]
        assert not is_safe_url("http://cgnat.example.com")
