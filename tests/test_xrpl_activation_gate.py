import os
import unittest
from unittest.mock import patch

from src.nothing_xrpl_worker import _require_live_activation


class XrplActivationGateTests(unittest.TestCase):
    def test_default_is_fail_closed(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                _require_live_activation()

    def test_unknown_mode_is_rejected(self):
        with patch.dict(
            os.environ,
            {"NOTHING_XRPL_ACTIVATION_MODE": "testnet"},
            clear=True,
        ):
            with self.assertRaises(RuntimeError):
                _require_live_activation()

    def test_mainnet_requires_explicit_ack(self):
        env = {
            "NOTHING_XRPL_ACTIVATION_MODE": "mainnet",
            "NOTHING_XRPL_ACCOUNT": "rEXAMPLE",
            "NOTHING_XRPL_RPC_URL": "https://example.invalid/",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(RuntimeError):
                _require_live_activation()

    def test_mainnet_activation_requires_explicit_runtime_values(self):
        base = {
            "NOTHING_XRPL_ACTIVATION_MODE": "mainnet",
            "NOTHING_XRPL_MAINNET_ACK": "I_UNDERSTAND_REAL_XRPL_PAYMENTS",
        }
        with patch.dict(os.environ, base, clear=True):
            with self.assertRaises(RuntimeError):
                _require_live_activation()

    def test_mainnet_activation_accepts_explicit_ack_and_runtime_values(self):
        env = {
            "NOTHING_XRPL_ACTIVATION_MODE": "mainnet",
            "NOTHING_XRPL_MAINNET_ACK": "I_UNDERSTAND_REAL_XRPL_PAYMENTS",
            "NOTHING_XRPL_ACCOUNT": "rEXAMPLE",
            "NOTHING_XRPL_RPC_URL": "https://example.invalid/",
        }
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(
                _require_live_activation(),
                ("rEXAMPLE", "https://example.invalid/"),
            )


if __name__ == "__main__":
    unittest.main()
