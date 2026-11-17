"""GET /photos/<key>: serves one listing photo.

The key must match photo_service.KEY_RE (32 hex characters + jpg/png/webp) BEFORE anything else
happens, so a path such as ../x can never reach the disk. A photo is only served while a listing
that is not removed uses it. The Content-Type comes from the key's extension, which was set from
the file's magic bytes when it was uploaded; X-Content-Type-Options: nosniff is added for every
response by app/security.py.
"""
from flask import Blueprint, abort, send_from_directory

from . import photo_service
from .db import get_db

bp = Blueprint("photos", __name__, url_prefix="/photos")


@bp.route("/<key>")
def serve(key):
    if not photo_service.KEY_RE.fullmatch(key):
        abort(404)
    if photo_service.visible_key(get_db(), key) is None:
        abort(404)
    ext = key.rsplit(".", 1)[1]
    response = send_from_directory(photo_service.photo_dir(), key, mimetype=photo_service.CONTENT_TYPES[ext],
                                   max_age=0, conditional=True)
    # no-cache = the browser may keep the file but must ask first, so a removed photo is gone at once
    # (a plain max-age let Chrome show a deleted photo from its cache); the ETag makes the answer a cheap 304.
    response.headers["Cache-Control"] = "private, no-cache"
    response.headers["Content-Disposition"] = "inline"
    return response
