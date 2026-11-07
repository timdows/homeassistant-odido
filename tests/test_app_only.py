"""Prevent reintroducing a selectable website transport or HTML usage fetching."""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "custom_components/odido_mobile_usage"


class AppOnlyTests(unittest.TestCase):
    def test_setup_has_no_website_fallback(self):
        setup = (ROOT / "__init__.py").read_text(encoding="utf-8")
        self.assertIn("client = OdidoAppClient(", setup)
        self.assertNotIn("client_type", setup)
        for filename in ROOT.glob("*.py"):
            source = filename.read_text(encoding="utf-8")
            for retired in (
                "/my/tegoed",
                "/my/verbruik-en-kosten",
                "BundleUsage.GetStatus",
                "SimUsageDetail.GetUsageTotals",
            ):
                self.assertNotIn(retired, source, str(filename))
