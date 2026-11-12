"""SSRF-safe target validation tests."""

import ipaddress
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from harness.errors import TargetError
from harness.orchestrator import Orchestrator, RunOptions
from harness.ssrf import load_allowlist, validate_redirect_chain, validate_url


class SsrfValidationTests(unittest.TestCase):
    def test_private_and_metadata_targets_are_rejected(self):
        for url in (
            "http://127.0.0.1:8080/",
            "http://localhost:8080/",
            "http://10.0.0.1/",
            "http://192.168.1.1/",
            "http://169.254.169.254/latest/meta-data/",
            "http://metadata.google.internal/",
        ):
            with self.assertRaises(TargetError):
                validate_url(url)
            # Error messages must not leak resolved internal addresses.
            try:
                validate_url(url)
            except TargetError as exc:
                self.assertNotIn("127.0.0.1", str(exc))
                self.assertNotIn("169.254", str(exc))

    def test_public_target_is_allowed(self):
        def resolver(host):
            return ["93.184.216.34"]

        self.assertEqual(
            validate_url("https://example.com/", resolver=resolver),
            "https://example.com/",
        )

    def test_localhost_requires_config_allowlist(self):
        config = Path(tempfile.mkdtemp()) / "ssrf-allowlist.json"
        config.write_text(json.dumps({"allow": ["127.0.0.0/8", "localhost"]}), encoding="utf-8")
        with mock.patch("harness.ssrf.socket.getaddrinfo", return_value=[(None, None, None, None, ("127.0.0.1", 0))]):
            url = validate_url("http://localhost:8080/", root=config.parent)
            self.assertEqual(url, "http://localhost:8080/")
        # Without allowlist, localhost remains blocked.
        with self.assertRaises(TargetError):
            validate_url("http://localhost:8080/")

    def test_cidr_allowlist_overrides_private_range(self):
        def resolver(host):
            return ["10.1.2.3"]

        url = validate_url(
            "http://internal.example/",
            allowlist=["10.0.0.0/8"],
            resolver=resolver,
        )
        self.assertEqual(url, "http://internal.example/")

    def test_dns_rebinding_is_checked_on_every_validation(self):
        addresses = iter(["93.184.216.34", "127.0.0.1"])

        def resolver(host):
            return [next(addresses)]

        self.assertTrue(validate_url("https://example.com/", resolver=resolver) is not None)
        with self.assertRaises(TargetError):
            validate_url("https://example.com/", resolver=resolver)

    def test_redirect_chain_is_revalidated(self):
        def resolver(host):
            return ["93.184.216.34"]

        with self.assertRaises(TargetError):
            validate_redirect_chain(
                [
                    "https://example.com/",
                    "http://127.0.0.1:8080/secret",
                ],
                resolver=resolver,
            )

    def test_orchestrator_rejects_unsafe_url_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            options = RunOptions(
                target="http://169.254.169.254/latest/meta-data/",
                personas_dir=str(Path.cwd() / "personas"),
                no_browser=True,
            )
            with self.assertRaises(TargetError):
                Orchestrator(root, options).run()

    def test_load_allowlist_reads_environment(self):
        with mock.patch.dict("os.environ", {"QH_SSRF_ALLOWLIST": "127.0.0.0/8,localhost"}, clear=False):
            self.assertEqual(load_allowlist(), ["127.0.0.0/8", "localhost"])


if __name__ == "__main__":
    unittest.main()
