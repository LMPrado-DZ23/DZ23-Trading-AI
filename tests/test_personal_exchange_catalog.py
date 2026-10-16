import unittest

from service.personal_exchange_catalog import EXCHANGES, get_personal_exchange, public_catalog


class PersonalExchangeCatalogTests(unittest.TestCase):
    def test_nine_brokers_plus_experimental_are_unique(self):
        self.assertEqual(len(EXCHANGES), 10)
        self.assertEqual(len({x.slug for x in EXCHANGES}), 10)

    def test_all_live_disabled(self):
        self.assertTrue(all(not row["live_enabled"] for row in public_catalog()))

    def test_iq_is_experimental_without_ccxt(self):
        iq = get_personal_exchange("iq_option")
        self.assertEqual(iq.market, "experimental")
        self.assertIsNone(iq.ccxt_id)

    def test_unknown_exchange_rejected(self):
        with self.assertRaises(KeyError):
            get_personal_exchange("invalid")

    def test_integrations_are_available_without_misrepresenting_live(self):
        catalogo = {x["id"]: x for x in public_catalog()}
        self.assertEqual(catalogo["binance"]["integration_status"],
                         "AVAILABLE_TESTNET")
        self.assertEqual(catalogo["kraken"]["integration_status"],
                         "AVAILABLE_READ_ONLY")
        self.assertEqual(catalogo["iq_option"]["integration_status"],
                         "EXPERIMENTAL_NOT_ELIGIBLE")
        self.assertFalse(catalogo["iq_option"]["read_only_enabled"])
        self.assertTrue(all(x["read_only_enabled"] for slug, x in
                            catalogo.items() if slug != "iq_option"))

if __name__ == "__main__":
    unittest.main()
