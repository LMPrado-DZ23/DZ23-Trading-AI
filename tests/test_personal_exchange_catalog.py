import unittest
from service.personal_exchange_catalog import EXCHANGES, get_personal_exchange, public_catalog

class PersonalExchangeCatalogTests(unittest.TestCase):
    def test_six_unique_exchanges(self):
        self.assertEqual(len(EXCHANGES), 6)
        self.assertEqual(len({x.slug for x in EXCHANGES}), 6)

    def test_all_live_disabled(self):
        self.assertTrue(all(not row["live_enabled"] for row in public_catalog()))

    def test_iq_is_experimental_without_ccxt(self):
        iq = get_personal_exchange("iq_option")
        self.assertEqual(iq.market, "experimental")
        self.assertIsNone(iq.ccxt_id)

    def test_unknown_exchange_rejected(self):
        with self.assertRaises(KeyError):
            get_personal_exchange("invalid")

    def test_integrations_are_not_misrepresented(self):
        self.assertTrue(all(x["integration_status"] == "PLANNED_NOT_VALIDATED" for x in public_catalog()))

if __name__ == "__main__":
    unittest.main()
