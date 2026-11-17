"""Configuration read from environment variables and validated at startup.

All variables are documented in .env.example. Nothing secret lives in the repo.
Validation collects EVERY problem and reports them together, so a bad deploy
shows the whole list at once instead of one error per restart.
"""
import os
import re
import secrets
from dataclasses import dataclass

ENVIRONMENTS = ("development", "testing", "production")
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
MIN_PRODUCTION_SECRET_LEN = 32
MAX_TRUSTED_PROXIES = 5
PLACEHOLDER_SECRETS = {"change-me", "changeme", "secret", "dev", "development", "password"}

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB_PATH = os.path.join(REPO_ROOT, "instance", "campus_barter.db")
MIGRATIONS_DIR = os.path.join(REPO_ROOT, "migrations")


class ConfigError(Exception):
    """Raised with a readable list of every configuration problem."""


@dataclass(frozen=True)
class Settings:
    env: str
    secret_key: str
    database_path: str
    secret_key_is_generated: bool = False
    log_level: str = "INFO"
    trusted_proxies: int = 0     # C23: how many reverse proxies in front of the app (0 = none, X-Forwarded-For ignored)

    @property
    def is_production(self):
        return self.env == "production"


def load_settings(environ=None):
    """Build Settings from an environment mapping (default: os.environ)."""
    env_map = os.environ if environ is None else environ
    problems = []

    env = (env_map.get("APP_ENV") or "development").strip().lower()
    if env not in ENVIRONMENTS:
        problems.append(f"APP_ENV must be one of {', '.join(ENVIRONMENTS)} (got {env!r})")
        env = "development"
    production = env == "production"

    secret = env_map.get("SECRET_KEY") or ""
    generated = False
    if production:
        if not secret:
            problems.append("SECRET_KEY is required in production")
        elif len(secret) < MIN_PRODUCTION_SECRET_LEN:
            problems.append(f"SECRET_KEY must be at least {MIN_PRODUCTION_SECRET_LEN} characters in production")
        elif secret.lower() in PLACEHOLDER_SECRETS:
            problems.append("SECRET_KEY is a placeholder value; generate a random one")
    elif not secret:
        secret, generated = secrets.token_hex(32), True  # per-process; sessions reset on restart

    db_path = (env_map.get("DATABASE_PATH") or DEFAULT_DB_PATH).strip()
    if not db_path:
        problems.append("DATABASE_PATH must not be empty")

    log_level = (env_map.get("LOG_LEVEL") or "INFO").strip().upper()
    if log_level not in LOG_LEVELS:
        problems.append(f"LOG_LEVEL must be one of {', '.join(LOG_LEVELS)} (got {log_level!r})")
        log_level = "INFO"

    proxies_text = (env_map.get("TRUSTED_PROXIES") or "0").strip()
    trusted_proxies = 0
    if proxies_text.isascii() and proxies_text.isdigit() and int(proxies_text) <= MAX_TRUSTED_PROXIES:
        trusted_proxies = int(proxies_text)
    else:
        problems.append(f"TRUSTED_PROXIES must be a whole number from 0 to {MAX_TRUSTED_PROXIES} (got {proxies_text!r})")

    flask_debug = (env_map.get("FLASK_DEBUG") or "").strip().lower()   # same test Flask itself uses
    if production and flask_debug not in ("", "0", "false", "no"):
        problems.append("FLASK_DEBUG must be off in production (debug mode shows internal details to visitors)")

    if problems:
        raise ConfigError("Invalid configuration:\n  - " + "\n  - ".join(problems))
    return Settings(env=env, secret_key=secret, database_path=db_path,
                    secret_key_is_generated=generated, log_level=log_level,
                    trusted_proxies=trusted_proxies)
