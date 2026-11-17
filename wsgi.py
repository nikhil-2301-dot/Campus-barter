"""Entry point for a production server:  gunicorn wsgi:app  (see start.sh and docs/runbooks/deploy.md)."""
from app import create_app

app = create_app()
