import unittest

from service.personal_exchange_catalog import (
    EXCHANGES,
    get_personal_exchange,
    public_catalog,
    required_execution_credentials,
)


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

    def test_credenciais_exigidas_por_cada_corretora(self):
        com_passphrase = {"okx", "coinbase", "kucoin", "bitget"}
        for exchange in EXCHANGES:
            campos = required_execution_credentials(exchange.slug)
            if exchange.slug == "iq_option":
                self.assertEqual(campos, ())
                continue
            self.assertEqual(campos[:2], (
                "EXCHANGE_API_KEY", "EXCHANGE_API_SECRET"))
            self.assertEqual("EXCHANGE_API_PASSWORD" in campos,
                             exchange.slug in com_passphrase)

    def test_catalogo_confere_com_o_ccxt_instalado_nas_nove_corretoras(self):
        import ccxt

        for exchange in EXCHANGES:
            if exchange.ccxt_id is None:
                continue
            cliente = getattr(ccxt, exchange.ccxt_id)()
            requisitos = cliente.requiredCredentials
            self.assertTrue(requisitos["apiKey"], exchange.slug)
            self.assertTrue(requisitos["secret"], exchange.slug)
            self.assertEqual(bool(requisitos.get("password")),
                             exchange.requires_password, exchange.slug)
            self.assertEqual(bool((cliente.urls or {}).get("test")),
                             exchange.testnet_enabled, exchange.slug)

if __name__ == "__main__":
    unittest.main()
