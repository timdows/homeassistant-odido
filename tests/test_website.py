"""Synthetic fixtures matching observed page structure; no account data."""

import unittest

from custom_components.odido_mobile_usage.website import UsageParseError, parse_usage

ALLOWANCE = """<main><div id="internet"><svg><text>999</text></svg>
<h4>7.500 MB <span>over van 10.000 MB</span></h4>
<unity-price>€ 1,25</unity-price></div>
<div id="calltext"><h4>Onbeperkt</h4></div></main>"""
DETAILS = """<main><h1>Verbruik &amp; Kosten</h1><table>
<tr><td>Mijn Simkaart</td><td>Data 3000 MB</td></tr>
<tr><td>Totaal</td><td>Data <span>3000 MB</span></td>
<td>Belminuten <span>26:41 min / 1 sms</span></td>
<td>Extra Kosten <span>€ 1,25</span></td></tr></table></main>"""


class ParserTests(unittest.TestCase):
    def test_css_table_labels_are_read_from_attributes(self):
        details = '''<main><table><tr><td>Totaal</td>
        <td data-table-header="Data"><span>3000 MB</span></td>
        <td data-table-header="Belminuten"><span>26:41 min / 1 sms</span></td>
        <td data-table-header="Extra Kosten"><span>&euro; 1,25</span></td>
        <td></td><td></td></tr></table></main>'''
        result = parse_usage(ALLOWANCE, details)
        self.assertEqual(result.reported_data_used_gb, 3)
        self.assertAlmostEqual(result.voice_used_minutes, 26 + 41/60, places=5)
        self.assertEqual(result.extra_costs, 1.25)

    def test_separate_allowance_and_monthly_usage(self):
        result = parse_usage(ALLOWANCE, DETAILS)
        self.assertEqual(result.limit_gb, 10)
        self.assertEqual(result.remaining_gb, 7.5)
        self.assertEqual(result.used_gb, 2.5)
        self.assertEqual(result.reported_data_used_gb, 3)
        self.assertEqual(result.percentage, 25)
        self.assertAlmostEqual(result.voice_used_minutes, 26 + 41 / 60, places=5)
        self.assertEqual(result.extra_costs, 1.25)
        self.assertEqual(result.voice_allowance, "Onbeperkt")
        self.assertEqual(result.sms_used, 1)

    def test_dutch_decimal_gigabytes(self):
        result = parse_usage(
            ALLOWANCE.replace("7.500 MB", "7,5 GB").replace("10.000 MB", "10 GB"),
            DETAILS,
        )
        self.assertEqual(result.remaining_gb, 7.5)

    def test_zero_is_not_missing(self):
        result = parse_usage(
            ALLOWANCE.replace("7.500 MB", "0 MB"),
            DETAILS.replace("€ 1,25", "€ 0,00").replace("26:41", "0:00"),
        )
        self.assertEqual(result.percentage, 100)
        self.assertEqual(result.extra_costs, 0)
        self.assertEqual(result.voice_used_minutes, 0)

    def test_unlimited_not_assumed_for_other_plans(self):
        self.assertIsNone(
            parse_usage(
                ALLOWANCE.replace("<h4>Onbeperkt</h4>", "<h4>100 minuten</h4>"), DETAILS
            ).voice_allowance
        )

    def test_invalid_or_ambiguous_pages_fail(self):
        for allowance, details in [
            ("<form>Login</form>", DETAILS),
            (ALLOWANCE, "<main>maintenance</main>"),
            (ALLOWANCE.replace("7.500", "12.000"), DETAILS),
            (ALLOWANCE.replace("10.000", "0"), DETAILS),
            (
                ALLOWANCE.replace("</div>", "<h4>1 GB over van 2 GB</h4></div>", 1),
                DETAILS,
            ),
            (ALLOWANCE, DETAILS.replace("26:41", "26:99")),
        ]:
            with (
                self.subTest(allowance=allowance, details=details),
                self.assertRaises(UsageParseError),
            ):
                parse_usage(allowance, details)

    def test_scripts_and_footer_do_not_become_usage(self):
        noisy = (
            "<script>Data 99 GB; Extra Kosten € 99,00</script><footer>90% data</footer>"
        )
        self.assertEqual(
            parse_usage(noisy + ALLOWANCE, noisy + DETAILS).extra_costs, 1.25
        )

    def test_no_identifiers_in_normalized_data(self):
        data = parse_usage(ALLOWANCE, DETAILS).as_dict()
        self.assertNotIn("phone_number", data)
        self.assertNotIn("cookies", data)


if __name__ == "__main__":
    unittest.main()
