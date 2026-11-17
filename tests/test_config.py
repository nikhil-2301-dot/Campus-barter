import unittest

import helpers  # noqa: F401  (sets sys.path)
from app.config import ConfigError, load_settings

GOOD_KEY = "k" * 40


class DevelopmentConfigTests(unittest.TestCase):
    def test_empty_environment_gives_safe_dev_defaults(self):
        s = load_settings({})
        self.assertEqual(s.env, "development")
        self.assertTrue(s.secret_key_is_generated)
        self.assertGreaterEqual(len(s.secret_key), 32)
        self.assertFalse(hasattr(s, "allowed_email_domains"))
        self.assertTrue(s.database_path.endswith("campus_barter.db"))

    def test_generated_keys_differ_between_loads(self):
        self.assertNotEqual(load_settings({}).secret_key, load_settings({}).secret_key)

    def test_explicit_values_are_used(self):
        s = load_settings({"SECRET_KEY": "abc", "DATABASE_PATH": "/tmp/x.db"})
        self.assertEqual(s.secret_key, "abc")
        self.assertFalse(s.secret_key_is_generated)
        self.assertEqual(s.database_path, "/tmp/x.db")

    def test_unknown_environment_name_is_refused(self):
        with self.assertRaises(ConfigError) as cm:
            load_settings({"APP_ENV": "staging"})
        self.assertIn("APP_ENV", str(cm.exception))


class ProductionConfigTests(unittest.TestCase):
    def prod(self, **kw):
        env = {"APP_ENV": "production", "SECRET_KEY": GOOD_KEY}
        env.update(kw)
        return load_settings(env)

    def test_valid_production_config_loads(self):
        s = self.prod()
        self.assertTrue(s.is_production)
        self.assertFalse(s.secret_key_is_generated)

    def test_missing_secret_key_is_refused(self):
        with self.assertRaises(ConfigError) as cm:
            self.prod(SECRET_KEY="")
        self.assertIn("SECRET_KEY is required", str(cm.exception))

    def test_short_secret_key_is_refused(self):
        with self.assertRaises(ConfigError):
            self.prod(SECRET_KEY="short")

    def test_placeholder_secret_key_is_refused(self):
        with self.assertRaises(ConfigError):
            self.prod(SECRET_KEY="change-me")

    def test_the_old_domain_setting_is_ignored(self):
        s = self.prod(ALLOWED_EMAIL_DOMAINS="@bad")
        self.assertTrue(s.is_production)

    def test_every_problem_is_reported_together(self):
        with self.assertRaises(ConfigError) as cm:
            load_settings({"APP_ENV": "production"})
        msg = str(cm.exception)
        self.assertIn("SECRET_KEY", msg)
        self.assertNotIn("ALLOWED_EMAIL_DOMAINS", msg)



if __name__ == "__main__":
    unittest.main()
