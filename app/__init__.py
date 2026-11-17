"""Campus Barter application factory.

Run:  flask --app app run --debug
"""
import os
from datetime import timedelta

from flask import Flask

from . import db as db_module
from .photo_service import MAX_REQUEST_BYTES
from .config import MIGRATIONS_DIR, ConfigError, load_settings  # noqa: F401  (ConfigError re-exported)


def create_app(test_config=None, environ=None):
    """Build the Flask app.

    `environ` lets tests pass a fake environment; `test_config` overrides final values
    (tests use it to point DATABASE_PATH at a temp file).
    """
    settings = load_settings(environ)
    app = Flask(__name__, instance_relative_config=False)
    app.config.update(
        APP_ENV=settings.env,
        SECRET_KEY=settings.secret_key,
        DATABASE_PATH=settings.database_path,
        LOG_LEVEL=settings.log_level,
        MIGRATIONS_DIR=MIGRATIONS_DIR,
        SEED_PASSWORD=(environ if environ is not None else os.environ).get("SEED_PASSWORD") or None,
        PERMANENT_SESSION_LIFETIME=timedelta(days=7),   # sliding: renewed on every request
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=settings.is_production,   # production runs over HTTPS only
        MAX_CONTENT_LENGTH=MAX_REQUEST_BYTES,           # C19: a photo is at most 2 MB; bigger requests get the 413 page
    )
    if test_config:
        app.config.update(test_config)

    if settings.trusted_proxies:   # C23: behind a reverse proxy, take the client address from X-Forwarded-For
        from werkzeug.middleware.proxy_fix import ProxyFix
        n = settings.trusted_proxies
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=n, x_proto=n, x_host=0, x_port=0, x_prefix=0)

    from . import logs
    logs.init_app(app)     # first, so the request id exists before any other hook and is set last

    if settings.secret_key_is_generated:
        app.logger.warning("SECRET_KEY not set: using a random per-process key "
                           "(fine for development; sessions reset on restart)")

    app.teardown_appcontext(db_module.close_db)

    from . import auth, problem_pages, security
    security.init_app(app)
    problem_pages.init_app(app)
    app.before_request(auth.load_user)

    from .health import bp as health_bp
    from .main import bp as main_bp
    from .listings import bp as listings_bp
    from .exchange import bp as exchange_bp
    from .reports import bp as reports_bp
    from .admin import bp as admin_bp
    from .photos import bp as photos_bp
    from .campuses import bp as campuses_bp
    app.register_blueprint(health_bp)
    app.register_blueprint(auth.bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(listings_bp)
    app.register_blueprint(exchange_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(photos_bp)
    app.register_blueprint(campuses_bp)
    if not settings.is_production:
        from .design import bp as design_bp
        app.register_blueprint(design_bp)   # component gallery: development and test only

    from . import cli
    cli.register(app)
    return app
