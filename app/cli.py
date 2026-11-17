"""Flask CLI commands:  flask --app app migrate | seed | create-admin"""
import click
from flask import current_app

from . import accounts, db, migrate as migrate_mod, seed as seed_mod


def register(app):
    @app.cli.command("migrate")
    def migrate_cmd():
        """Apply pending database migrations."""
        conn = db.connect(current_app.config["DATABASE_PATH"])
        try:
            done = migrate_mod.migrate(conn, current_app.config["MIGRATIONS_DIR"])
        except migrate_mod.MigrationProblem as exc:
            raise click.ClickException(str(exc))
        finally:
            conn.close()
        click.echo("applied: " + ", ".join(done) if done else "database is up to date")

    @app.cli.command("seed")
    def seed_cmd():
        """Insert demo users and listings (development only)."""
        conn = db.connect(current_app.config["DATABASE_PATH"])
        try:
            created, password = seed_mod.seed(
                conn, env=current_app.config["APP_ENV"],
                domain=seed_mod.DEMO_DOMAIN,
                password=current_app.config.get("SEED_PASSWORD"))
        except seed_mod.SeedRefused as exc:
            raise click.ClickException(str(exc))
        finally:
            conn.close()
        if not created:
            click.echo("demo data already present; nothing changed")
        else:
            click.echo("demo data created. Demo password (shown once): " + password)

    @app.cli.command("create-admin")
    @click.option("--email", prompt="Email", help="Any valid email address.")
    @click.option("--name", "full_name", prompt="Full name")
    @click.option("--college", "college_name", default="Administration", show_default=True,
                  help="College name stored on the admin profile.")
    @click.option("--enrollment", "enrollment_no", default="ADMIN001", show_default=True,
                  help="Enrollment number stored on the admin profile.")
    def create_admin_cmd(email, full_name, college_name, enrollment_no):
        """Create an admin account, or promote an existing user. The password is asked for
        (hidden), never taken from the command line, so it stays out of shell history."""
        email = accounts.normalize_email(email)
        problem = accounts.check_email(email)
        if problem:
            raise click.ClickException(problem)
        conn = db.connect(current_app.config["DATABASE_PATH"])
        try:
            existing = conn.execute("SELECT id, is_admin FROM users WHERE email = ? AND deleted_at IS NULL",
                                    (email,)).fetchone()
            if existing:
                conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (existing["id"],))
                conn.commit()
                click.echo("existing user is now an admin: " + email)
                return
            clean, problems = accounts.validate_profile(
                {"full_name": full_name, "branch": "Administration", "year": "BE",
                 "college_name": college_name, "enrollment_no": enrollment_no})
            for field, msg in problems.items():
                raise click.ClickException(f"{field}: {msg}")
            password = click.prompt("Password", hide_input=True, confirmation_prompt=True)
            problem = accounts.check_password(password, None, email)
            if problem:
                raise click.ClickException(problem)
            try:
                accounts.create_user(conn, email=email, password=password, is_admin=True, **clean)
            except accounts.EnrollmentTaken:
                raise click.ClickException(
                    "enrollment number " + clean["enrollment_no"] + " is already used; "
                    "pass a different one with --enrollment")
        finally:
            conn.close()
        click.echo("admin created: " + email)
