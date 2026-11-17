"""C19: listing photos. Type by magic bytes, size limit, random names, the serving route,
owner-only changes, deleting files, alt text, rate limits and the query count.
(Keep the words error / failed / skipped out of names and docstrings: scripts/check.sh gates on them.)"""
import io
import os
import re
import tempfile
import unittest

import helpers
from app import photo_service
from app.db import connect
from scripts import ui_audit
from test_auth import token_from
from test_browse_ui import ADMIN, ASKER, OWNER, BrowseCase, page
from test_query_budget import get_counted

JPEG = b"\xff\xd8\xff\xe0" + bytes(64)
PNG = b"\x89PNG\r\n\x1a\n" + bytes(64)
WEBP = b"RIFF" + bytes(4) + b"WEBP" + bytes(32)
GOOD = {"title": "Mini drafter", "category": "Mini drafter", "condition": "good", "mode": "give",
        "description": "Barely used.", "wanted_in_return": ""}
KEY = re.compile(r"/photos/([0-9a-f]{32}\.(?:jpg|png|webp))")


class PhotoCase(BrowseCase):
    def setUp(self):
        super().setUp()
        self.owner = self.app.test_client()
        self.login(email=OWNER, client=self.owner)

    def photo_files(self):
        folder = os.path.join(self.tmp, "photos")
        return sorted(os.listdir(folder)) if os.path.isdir(folder) else []

    def send(self, url, content=None, filename="photo.jpg", client=None, fields=None, content_type=None):
        data = dict(GOOD, **(fields or {}))
        if content is not None:
            data["photo"] = (io.BytesIO(content), filename, content_type) if content_type else (io.BytesIO(content), filename)
        return self.post(url, data, client=client or self.owner, page=url, content_type="multipart/form-data")

    def create(self, content=None, **kw):
        r = self.send("/listings/new", content, **kw)
        return r

    def listing_with_photo(self, content=JPEG, filename="a.jpg"):
        r = self.create(content, filename=filename)
        self.assertEqual(r.status_code, 303)
        row = self.db_rows("SELECT * FROM listings ORDER BY id DESC LIMIT 1")[0]
        return row["id"], row["photo_key"]

    def key_of(self, listing_id):
        return self.db_rows("SELECT photo_key FROM listings WHERE id = ?", (listing_id,))[0]["photo_key"]

    def fetch(self, path, client=None):
        """GET, read the whole body and close the response (the file behind it is closed too)."""
        r = (client or self.client).get(path)
        r.get_data()
        r.close()
        return r

    def listing_count(self):
        return self.db_rows("SELECT COUNT(*) AS n FROM listings")[0]["n"]


class AcceptedTypeTests(PhotoCase):
    def test_each_allowed_type_is_stored_under_a_random_name(self):
        for content, ext in ((JPEG, "jpg"), (PNG, "png"), (WEBP, "webp")):
            _lid, key = self.listing_with_photo(content, filename="whatever.bin")
            self.assertRegex(key, r"^[0-9a-f]{32}\." + ext + "$")
            with open(os.path.join(self.tmp, "photos", key), "rb") as fh:
                self.assertEqual(fh.read(), content)
        self.assertEqual(len(set(self.photo_files())), 3)

    def test_no_file_chosen_makes_a_listing_without_a_photo(self):
        r = self.create(None)
        self.assertEqual(r.status_code, 303)
        self.assertIsNone(self.db_rows("SELECT photo_key FROM listings")[0]["photo_key"])
        self.assertEqual(self.photo_files(), [])

    def test_the_file_name_never_reaches_the_stored_name(self):
        _lid, key = self.listing_with_photo(JPEG, filename="../../evil name.php")
        self.assertRegex(key, r"^[0-9a-f]{32}\.jpg$")

    def test_the_browser_content_type_is_not_trusted(self):
        _lid, key = self.listing_with_photo()
        r = self.create(PNG, filename="x.jpg", content_type="text/plain")
        self.assertEqual(r.status_code, 303)
        self.assertTrue(self.key_of(self.listing_count()).endswith(".png"))
        self.assertTrue(key.endswith(".jpg"))


class RefusedTypeTests(PhotoCase):
    def check_refused(self, content, filename, **kw):
        before = self.listing_count()
        r = self.create(content, filename=filename, **kw)
        self.assertEqual(r.status_code, 400)
        doc = page(r.get_data(as_text=True))
        self.assertIn("not a photo", doc.select_one("#p-photo").get_text())
        self.assertIn("p-photo", doc.select_one("#f-photo")["aria-describedby"].split())
        self.assertEqual(self.listing_count(), before)
        self.assertEqual(self.photo_files(), [])

    def test_a_text_file_with_a_photo_extension_is_refused(self):
        self.check_refused(b"just some text, not a picture", "holiday.jpg")

    def test_html_sent_as_an_image_is_refused(self):
        self.check_refused(b"<html><script>alert(1)</script></html>", "x.png", content_type="image/png")

    def test_other_picture_types_are_refused(self):
        self.check_refused(b"GIF89a" + bytes(40), "x.gif")
        self.check_refused(b"BM" + bytes(40), "x.bmp")
        self.check_refused(b"<svg xmlns='http://www.w3.org/2000/svg'></svg>", "x.svg")

    def test_a_riff_file_that_is_not_webp_is_refused(self):
        self.check_refused(b"RIFF" + bytes(4) + b"WAVE" + bytes(32), "x.webp")

    def test_an_empty_file_is_refused(self):
        before = self.listing_count()
        r = self.create(b"", filename="empty.jpg")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.listing_count(), before)

    def test_a_refused_photo_keeps_what_was_typed(self):
        r = self.create(b"nope", filename="x.jpg", fields={"title": "Kept title"})
        self.assertEqual(page(r.get_data(as_text=True)).select_one("#f-title")["value"], "Kept title")


class SizeTests(PhotoCase):
    def test_a_photo_of_exactly_the_limit_is_accepted(self):
        content = JPEG + bytes(photo_service.MAX_BYTES - len(JPEG))
        self.assertEqual(len(content), photo_service.MAX_BYTES)
        self.assertEqual(self.create(content).status_code, 303)

    def test_one_byte_over_the_limit_is_refused_next_to_the_field(self):
        content = JPEG + bytes(photo_service.MAX_BYTES - len(JPEG) + 1)
        r = self.create(content)
        self.assertEqual(r.status_code, 400)
        self.assertIn("larger than 2 MB", page(r.get_data(as_text=True)).select_one("#p-photo").get_text())
        self.assertEqual(self.listing_count(), 0)
        self.assertEqual(self.photo_files(), [])

    def test_a_much_bigger_request_gets_the_styled_413_page(self):
        content = JPEG + bytes(photo_service.MAX_REQUEST_BYTES + 10)
        r = self.create(content)
        self.assertEqual(r.status_code, 413)
        doc = page(r.get_data(as_text=True))
        self.assertEqual(len(doc.select("h1")), 1)
        self.assertIn("too big", doc.select_one("h1").get_text())
        self.assertIn("2 MB", doc.get_text())
        self.assertEqual(self.listing_count(), 0)
        self.assertEqual(self.photo_files(), [])

    def test_the_form_page_states_the_limit(self):
        doc = page(self.owner.get("/listings/new").get_data(as_text=True))
        self.assertIn("2 MB", doc.select_one("#h-photo").get_text())
        self.assertEqual(doc.select_one("main form")["enctype"], "multipart/form-data")
        self.assertEqual(doc.select_one("#f-photo")["accept"], "image/jpeg,image/png,image/webp")


class ServingTests(PhotoCase):
    def test_the_photo_is_served_with_the_right_headers(self):
        lid, key = self.listing_with_photo(PNG, filename="a.png")
        r = self.fetch("/photos/" + key)    # a guest: browsing is public
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data, PNG)
        self.assertEqual(r.headers["Content-Type"], "image/png")
        self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(r.headers["Cache-Control"], "private, no-cache")
        self.assertEqual(r.headers["Content-Disposition"], "inline")

    def test_each_type_has_its_content_type(self):
        for content, mime in ((JPEG, "image/jpeg"), (PNG, "image/png"), (WEBP, "image/webp")):
            _lid, key = self.listing_with_photo(content)
            self.assertEqual(self.fetch("/photos/" + key).headers["Content-Type"], mime)

    def test_path_traversal_and_odd_names_get_404(self):
        _lid, key = self.listing_with_photo()
        stem = key.split(".")[0]
        for bad in ("../app/config.py", "..%2fapp%2fconfig.py", "%2e%2e%2f%2e%2e%2fetc%2fpasswd", "..\\x.jpg",
                    key.upper(), stem, stem + ".gif", stem + ".jpg.txt", stem[:-1] + ".jpg",
                    stem + ".jpg%00.png", "a" * 300 + ".jpg", ".jpg", "x.jpg"):
            self.assertEqual(self.fetch("/photos/" + bad).status_code, 404, bad)

    def test_a_file_no_listing_uses_is_not_served(self):
        os.makedirs(os.path.join(self.tmp, "photos"), exist_ok=True)
        key = "0" * 32 + ".jpg"
        with open(os.path.join(self.tmp, "photos", key), "wb") as fh:
            fh.write(JPEG)
        self.assertEqual(self.fetch("/photos/" + key).status_code, 404)

    def test_a_removed_listing_does_not_serve_its_photo(self):
        lid, key = self.listing_with_photo()
        self.sql("UPDATE listings SET status = 'removed', deleted_at = '2026-01-01T00:00:00.000Z' WHERE id = ?", (lid,))
        self.assertEqual(self.fetch("/photos/" + key).status_code, 404)

    def test_a_reserved_listing_still_shows_its_photo(self):
        lid, key = self.listing_with_photo()
        self.sql("UPDATE listings SET status = 'reserved' WHERE id = ?", (lid,))
        self.assertEqual(self.fetch("/photos/" + key).status_code, 200)

    def test_a_browser_that_asks_again_is_told_the_photo_is_gone(self):
        lid, key = self.listing_with_photo()
        first = self.client.get("/photos/" + key)
        first.close()
        etag = first.headers["ETag"]
        again = self.client.get("/photos/" + key, headers={"If-None-Match": etag})
        again.close()
        self.assertEqual(again.status_code, 304)
        self.post(f"/listings/{lid}/delete", {}, client=self.owner, page=f"/listings/{lid}/delete")
        gone = self.client.get("/photos/" + key, headers={"If-None-Match": etag})
        gone.close()
        self.assertEqual(gone.status_code, 404)

    def test_the_route_answers_get_only(self):
        _lid, key = self.listing_with_photo()
        self.assertEqual(self.owner.post("/photos/" + key, data={}).status_code, 405)


class DisplayTests(PhotoCase):
    def test_card_and_detail_page_show_the_photo_with_alt_text(self):
        lid, key = self.listing_with_photo()
        card = page(self.client.get("/listings/").get_data(as_text=True)).select_one(".listing-card img")
        self.assertEqual(card["src"], "/photos/" + key)
        self.assertEqual(card["alt"], "Photo of Mini drafter")
        self.assertIn("photo-card", card["class"])
        big = page(self.client.get(f"/listings/{lid}").get_data(as_text=True)).select_one("figure.listing-photo img")
        self.assertEqual(big["src"], "/photos/" + key)
        self.assertEqual(big["alt"], "Photo of Mini drafter")
        self.assertIn("photo-detail", big["class"])

    def test_a_listing_without_a_photo_shows_no_image(self):
        lid = self.seed(1)[0]
        self.assertEqual(page(self.client.get("/listings/").get_data(as_text=True)).select("img"), [])
        self.assertEqual(page(self.client.get(f"/listings/{lid}").get_data(as_text=True)).select("figure img"), [])

    def test_the_alt_text_is_escaped(self):
        r = self.create(JPEG, fields={"title": '"><script>alert(1)</script>'})
        self.assertEqual(r.status_code, 303)
        html = self.client.get("/listings/").get_data(as_text=True)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("Photo of &#34;&gt;&lt;script&gt;", html)

    def test_the_audit_alt_rule_is_proven_on_real_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, ids = ui_audit.build_audit_app(tmp)
            client = app.test_client()
            for path in ("/listings/?q=Mathematics", f"/listings/{ids['listing']['math']}"):
                html = client.get(path).get_data(as_text=True)
                imgs = page(html).select("img.photo")
                self.assertTrue(imgs, path)
                for img in imgs:
                    self.assertEqual(img["alt"], "Photo of Engineering Mathematics textbook")
                self.assertEqual([f for f in ui_audit.audit_html(html, path) if f.rule == "alt"], [])
            image = client.get("/photos/" + ids["photo_key"])
            image.close()
            self.assertEqual(image.status_code, 200)

    def test_the_design_page_shows_the_upload_field(self):
        doc = page(self.client.get("/design").get_data(as_text=True))
        field = doc.select_one("#f-demo_photo")
        self.assertEqual(field["type"], "file")
        self.assertIsNotNone(doc.select_one('label[for="f-demo_photo"]'))
        self.assertIn("p-demo_photo_big", doc.select_one("#f-demo_photo_big")["aria-describedby"])


class ChangeTests(PhotoCase):
    def test_the_edit_page_shows_the_current_photo_and_a_remove_button(self):
        lid, key = self.listing_with_photo()
        doc = page(self.owner.get(f"/listings/{lid}/edit").get_data(as_text=True))
        self.assertEqual(doc.select_one(".current-photo img")["src"], "/photos/" + key)
        form = doc.select_one(f'form[action="/listings/{lid}/photo/remove"]')
        self.assertIsNotNone(form)
        self.assertTrue(form.select_one('input[name="csrf_token"]')["value"])

    def test_replacing_the_photo_deletes_the_old_file(self):
        lid, old = self.listing_with_photo(JPEG)
        r = self.send(f"/listings/{lid}/edit", PNG, filename="n.png")
        self.assertEqual(r.status_code, 303)
        new = self.key_of(lid)
        self.assertNotEqual(new, old)
        self.assertTrue(new.endswith(".png"))
        self.assertEqual(self.photo_files(), [new])

    def test_saving_without_a_file_keeps_the_photo(self):
        lid, key = self.listing_with_photo()
        self.assertEqual(self.send(f"/listings/{lid}/edit", None, fields={"title": "New title"}).status_code, 303)
        self.assertEqual(self.key_of(lid), key)
        self.assertEqual(self.photo_files(), [key])

    def test_a_refused_replacement_keeps_the_old_photo(self):
        lid, key = self.listing_with_photo()
        r = self.send(f"/listings/{lid}/edit", b"not a picture", filename="x.png")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.key_of(lid), key)
        self.assertEqual(self.photo_files(), [key])

    def test_the_owner_can_remove_the_photo(self):
        lid, key = self.listing_with_photo()
        r = self.post(f"/listings/{lid}/photo/remove", {}, client=self.owner, page=f"/listings/{lid}/edit")
        self.assertEqual(r.status_code, 303)
        self.assertIsNone(self.key_of(lid))
        self.assertEqual(self.photo_files(), [])
        self.assertEqual(self.fetch("/photos/" + key).status_code, 404)

    def test_removing_when_there_is_no_photo_changes_nothing(self):
        lid = self.seed(1)[0]
        r = self.post(f"/listings/{lid}/photo/remove", {}, client=self.owner, page=f"/listings/{lid}/edit",
                      follow_redirects=True)
        self.assertIn("no photo", r.get_data(as_text=True))

    def test_removing_needs_a_post(self):
        lid, _key = self.listing_with_photo()
        self.assertEqual(self.owner.get(f"/listings/{lid}/photo/remove").status_code, 405)


class OwnerOnlyTests(PhotoCase):
    def setUp(self):
        super().setUp()
        self.lid, self.key = self.listing_with_photo()
        self.other = self.student_client(ASKER)

    def test_another_student_cannot_replace_or_remove(self):
        r = self.send(f"/listings/{self.lid}/edit", PNG, filename="n.png", client=self.other)
        self.assertEqual(r.status_code, 403)
        r = self.post(f"/listings/{self.lid}/photo/remove", {}, client=self.other, page="/listings/")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.key_of(self.lid), self.key)
        self.assertEqual(self.photo_files(), [self.key])

    def test_a_guest_is_sent_to_log_in(self):
        r = self.client.post(f"/listings/{self.lid}/photo/remove", data={})
        self.assertIn(r.status_code, (302, 303, 403))
        self.assertEqual(self.key_of(self.lid), self.key)
        r = self.client.get(f"/listings/{self.lid}/edit")
        self.assertIn(r.status_code, (302, 303))

    def test_a_suspended_student_cannot_post_a_new_listing_with_a_photo(self):
        token = token_from(self.owner.get("/listings/new").get_data(as_text=True))
        self.sql("UPDATE users SET is_suspended = 1 WHERE email = ?", (OWNER,))
        before = self.photo_files()
        data = dict(GOOD, photo=(io.BytesIO(PNG), "n.png"), csrf_token=token)
        r = self.owner.post("/listings/new", data=data, content_type="multipart/form-data")
        self.assertTrue(r.headers["Location"].startswith("/login"))   # a suspended account is signed out
        self.assertEqual(self.photo_files(), before)
        self.assertEqual(self.listing_count(), 1)

    def test_the_remove_button_needs_the_form_token(self):
        r = self.owner.post(f"/listings/{self.lid}/photo/remove", data={})
        self.assertEqual(r.status_code, 403)
        r = self.owner.post(f"/listings/{self.lid}/photo/remove", data={"csrf_token": "wrong"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.key_of(self.lid), self.key)

    def test_an_upload_needs_the_form_token(self):
        data = dict(GOOD, photo=(io.BytesIO(PNG), "n.png"))
        r = self.owner.post(f"/listings/{self.lid}/edit", data=data, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.photo_files(), [self.key])
        data = dict(GOOD, photo=(io.BytesIO(PNG), "n.png"))
        r = self.owner.post("/listings/new", data=data, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.photo_files(), [self.key])

    def test_an_admin_may_remove_a_photo_like_any_edit(self):
        self.make_user(ADMIN, is_admin=1)
        admin = self.app.test_client()
        self.login(email=ADMIN, client=admin)
        r = self.post(f"/listings/{self.lid}/photo/remove", {}, client=admin, page="/listings/")
        self.assertEqual(r.status_code, 303)
        self.assertIsNone(self.key_of(self.lid))
        self.assertEqual(self.photo_files(), [])


class DeleteWithListingTests(PhotoCase):
    def test_the_owner_deleting_the_listing_deletes_the_file(self):
        lid, key = self.listing_with_photo()
        r = self.post(f"/listings/{lid}/delete", {}, client=self.owner, page=f"/listings/{lid}/delete")
        self.assertEqual(r.status_code, 303)
        self.assertEqual(self.photo_files(), [])
        self.assertIsNone(self.key_of(lid))
        self.assertEqual(self.fetch("/photos/" + key).status_code, 404)

    def test_an_admin_removing_the_listing_deletes_the_file(self):
        lid, _key = self.listing_with_photo()
        self.make_user(ADMIN, is_admin=1)
        admin = self.app.test_client()
        self.login(email=ADMIN, client=admin)
        r = self.post(f"/admin/listings/{lid}/remove", {}, client=admin, page=f"/admin/listings/{lid}/remove")
        self.assertEqual(r.status_code, 303)
        self.assertEqual(self.photo_files(), [])

    def test_other_photos_stay(self):
        lid, _key = self.listing_with_photo(JPEG)
        _lid2, key2 = self.listing_with_photo(PNG, filename="b.png")
        self.post(f"/listings/{lid}/delete", {}, client=self.owner, page=f"/listings/{lid}/delete")
        self.assertEqual(self.photo_files(), [key2])


class LimitTests(PhotoCase):
    def test_the_hourly_listing_limit_still_applies_to_uploads(self):
        for _ in range(10):
            self.assertEqual(self.create(JPEG).status_code, 303)
        r = self.create(JPEG)
        self.assertEqual(r.status_code, 429)
        self.assertEqual(len(self.photo_files()), 10)
        self.assertEqual(self.listing_count(), 10)


class QueryCountTests(PhotoCase):
    def test_browse_runs_the_same_statements_with_or_without_photos(self):
        self.seed(10, prefix="Plain")
        plain, _ = get_counted(self.app, self.client, "/listings/")
        self.sql("UPDATE listings SET photo_key = id || '0000000000000000000000000000000.jpg'")
        with_photos, response = get_counted(self.app, self.client, "/listings/")
        self.assertEqual(response.get_data(as_text=True).count("<img"), 10)
        self.assertEqual(with_photos, plain)

    def test_the_photo_route_runs_one_statement(self):
        _lid, key = self.listing_with_photo()
        count, response = get_counted(self.app, self.client, "/photos/" + key)
        response.close()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(count, 1)


class RuleUnitTests(unittest.TestCase):
    def test_detect_type_reads_only_the_first_bytes(self):
        self.assertEqual(photo_service.detect_type(JPEG[:12]), "jpg")
        self.assertEqual(photo_service.detect_type(PNG[:12]), "png")
        self.assertEqual(photo_service.detect_type(WEBP[:12]), "webp")
        for other in (b"", b"GIF89a", b"\xff\xd8", b"RIFF\0\0\0\0WAVE", b"\x89PNG", b"%PDF-1.7"):
            self.assertIsNone(photo_service.detect_type(other), other)

    def test_key_shape(self):
        for good in ("a" * 32 + ".jpg", "0123456789abcdef" * 2 + ".webp"):
            self.assertTrue(photo_service.KEY_RE.fullmatch(good))
        for bad in ("a" * 31 + ".jpg", "A" * 32 + ".jpg", "a" * 32 + ".jpg\n", "../" + "a" * 32 + ".jpg",
                    "a" * 32 + ".exe", "a" * 32):
            self.assertIsNone(photo_service.KEY_RE.fullmatch(bad), bad)

    def test_alt_text(self):
        self.assertEqual(photo_service.alt_text("Lab coat"), "Photo of Lab coat")


if __name__ == "__main__":
    unittest.main()
