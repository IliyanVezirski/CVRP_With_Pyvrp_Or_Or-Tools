from types import SimpleNamespace
import unittest
from unittest.mock import patch

import config_gui
from config_gui import ConfigGUI, _validate_web_gui_credential_form


class CredentialFormValidationTests(unittest.TestCase):
    def test_password_is_returned_exactly_without_stripping_or_splitting(self):
        password = "  Сигурна парола:; 🔐  "

        username, validated_password = _validate_web_gui_credential_form(
            "  iliyan  ",
            password,
            password,
        )

        self.assertEqual(username, "iliyan")
        self.assertEqual(validated_password, password)

    def test_empty_mismatch_and_short_password_are_rejected(self):
        invalid_values = (
            ("", "long enough", "long enough"),
            ("user", "", ""),
            ("user", "long enough", "different"),
            ("user", "short", "short"),
        )
        for values in invalid_values:
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    _validate_web_gui_credential_form(*values)


class CredentialStoreWiringTests(unittest.TestCase):
    def test_store_is_lazy_cached_and_receives_legacy_value_once(self):
        gui = object.__new__(ConfigGUI)
        gui._web_gui_credential_store = None
        gui.cfg = SimpleNamespace(
            api=SimpleNamespace(web_gui_users="legacy: exact;password ")
        )
        fake_store = object()

        with patch.object(config_gui, "_base_dir", r"D:\TemporaryCVRP"), patch.object(
            config_gui,
            "CredentialStore",
            return_value=fake_store,
        ) as store_class:
            self.assertIs(gui._get_web_gui_credential_store(), fake_store)
            self.assertIs(gui._get_web_gui_credential_store(), fake_store)

        store_class.assert_called_once_with(
            r"D:\TemporaryCVRP",
            legacy_users="legacy: exact;password ",
        )


if __name__ == "__main__":
    unittest.main()
