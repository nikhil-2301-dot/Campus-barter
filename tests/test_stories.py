"""C21: whole stories with three students on the real routes, plus two simultaneous accepts
and the SQLite connection settings.

Reminder: scripts/check.sh treats certain words in the test log as a gate, so keep them out of
names and docstrings (see tests/helpers.py).
"""
import threading
import unittest

import helpers
from app import exchange_service as svc
from app.db import connect
from test_exchange import World, DOMAIN

PHONES = {"owner": "9111111111", "asker": "9222222222", "third": "9333333333"}
ADDRESSES = {"owner": "11 Owner Lane", "asker": "22 Asker Lane", "third": "33 Third Lane"}


class StoryWorld(World):
    """Three students with phone and address, and helpers that act like the browser does."""

    def setUp(self):
        super().setUp()
        for name in ("owner", "asker", "third"):
            setattr(self, name, self.student(name, phone=PHONES[name], address=ADDRESSES[name]))

    def ask(self, who, listing_id, offered=None, message="Hello, may I have this?"):
        data = {"message": message}
        if offered:
            data["offered_listing_id"] = str(offered)
        r = self.post(who, f"/listings/{listing_id}/request", data, page=f"/listings/{listing_id}")
        self.assertEqual(r.status_code, 303)
        row = self.conn.execute(
            "SELECT id FROM exchange_requests WHERE listing_id = ? AND requester_id = ? ORDER BY id DESC",
            (listing_id, getattr(self, who))).fetchone()
        return row["id"]

    def act(self, who, rid, action):
        return self.post(who, f"/requests/{rid}/{action}", page=f"/requests/{rid}")

    def page(self, who, rid):
        return self.get(who, f"/requests/{rid}").get_data(as_text=True)

    def assert_no_contact_of(self, html, *names):
        for name in names:
            for secret in (PHONES[name], ADDRESSES[name], f"{name}@{DOMAIN}"):
                self.assertNotIn(secret, html)

    def statuses(self, rid, *listing_ids):
        req = self.req(rid)["status"]
        return (req,) + tuple(self.lst(i)["status"] for i in listing_ids)


class SwapStory(StoryWorld, unittest.TestCase):
    def test_swap_from_request_to_both_items_closed(self):
        wanted = self.listing(self.owner, "Maths book", mode="swap")
        offered = self.listing(self.asker, "Physics book", mode="swap")
        rid = self.ask("asker", wanted, offered)
        self.assertEqual(self.statuses(rid, wanted, offered), ("pending", "available", "available"))
        self.assert_no_contact_of(self.page("owner", rid), "asker")
        self.assert_no_contact_of(self.page("asker", rid), "owner")

        self.assertEqual(self.act("owner", rid, "accept").status_code, 303)
        self.assertEqual(self.statuses(rid, wanted, offered), ("accepted", "reserved", "reserved"))
        for secret in (PHONES["asker"], ADDRESSES["asker"]):
            self.assertIn(secret, self.page("owner", rid))
        for secret in (PHONES["owner"], ADDRESSES["owner"]):
            self.assertIn(secret, self.page("asker", rid))
        stranger = self.get("third", f"/requests/{rid}")
        self.assertEqual(stranger.status_code, 404)
        self.assert_no_contact_of(stranger.get_data(as_text=True), "owner", "asker")

        self.assertEqual(self.act("asker", rid, "complete").status_code, 303)
        self.assertEqual(self.statuses(rid, wanted, offered), ("completed", "closed", "closed"))


class LendStory(StoryWorld, unittest.TestCase):
    def test_lend_comes_back_and_can_be_borrowed_again(self):
        lid = self.listing(self.owner, "Drafter", mode="lend")
        rid = self.ask("asker", lid)
        self.act("owner", rid, "accept")
        self.assertEqual(self.statuses(rid, lid), ("accepted", "reserved"))
        self.assertEqual(self.get("third", f"/listings/{lid}").status_code, 200)
        self.act("owner", rid, "complete")                      # marked as returned
        self.assertEqual(self.statuses(rid, lid), ("completed", "available"))
        again = self.ask("third", lid)
        self.assertEqual(self.req(again)["status"], "pending")


class DeclineStory(StoryWorld, unittest.TestCase):
    def test_declined_request_leaks_nothing_and_can_be_asked_again(self):
        lid = self.listing(self.owner, "Notes", mode="give")
        rid = self.ask("asker", lid)
        self.assertEqual(self.act("owner", rid, "decline").status_code, 303)
        self.assertEqual(self.statuses(rid, lid), ("declined", "available"))
        self.assert_no_contact_of(self.page("asker", rid), "owner")
        self.assert_no_contact_of(self.page("owner", rid), "asker")
        second = self.ask("asker", lid, message="Please reconsider")
        self.assertNotEqual(second, rid)
        self.assertEqual(self.req(second)["status"], "pending")


class CancelStory(StoryWorld, unittest.TestCase):
    def test_cancel_after_accept_frees_the_item_and_hides_contacts(self):
        lid = self.listing(self.owner, "Lab coat", mode="give")
        rid = self.ask("asker", lid)
        self.act("owner", rid, "accept")
        self.assertEqual(self.act("asker", rid, "cancel").status_code, 303)
        self.assertEqual(self.statuses(rid, lid), ("cancelled", "available"))
        self.assert_no_contact_of(self.page("owner", rid), "asker")
        self.assert_no_contact_of(self.page("asker", rid), "owner")
        other = self.ask("third", lid)                           # the item can go to someone else
        self.act("owner", other, "accept")
        self.assertEqual(self.statuses(other, lid), ("accepted", "reserved"))

    def test_cancel_while_pending_changes_only_the_request(self):
        lid = self.listing(self.owner, "Calculator", mode="give")
        rid = self.ask("asker", lid)
        self.act("asker", rid, "cancel")
        self.assertEqual(self.statuses(rid, lid), ("cancelled", "available"))


class ReportStory(StoryWorld, unittest.TestCase):
    def report(self, who, lid, reason="This listing looks like a scam to me."):
        return self.post(who, f"/listings/{lid}/report", {"reason": reason}, page=f"/listings/{lid}")

    def test_report_then_admin_removes_the_listing(self):
        admin = self.student("admin")
        self.conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (admin,))
        self.conn.commit()
        lid = self.listing(self.owner, "Suspicious item", mode="give")
        rid = self.ask("asker", lid)
        self.assertEqual(self.report("third", lid).status_code, 303)
        self.assertEqual(self.report("third", lid, "A second and different reason.").status_code, 303)
        self.assertIn("already reported", self.get("third", f"/listings/{lid}").get_data(as_text=True))
        self.assertEqual(self.report("owner", lid).status_code, 403)        # not your own listing
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0], 1)

        self.assertEqual(self.get("third", "/admin/reports").status_code, 403)
        self.assertEqual(self.get("admin", "/admin/reports").status_code, 200)
        r = self.post("admin", f"/admin/listings/{lid}/remove", page=f"/admin/listings/{lid}/remove")
        self.assertEqual(r.status_code, 303)
        self.assertEqual(self.statuses(rid, lid), ("declined", "removed"))
        self.assertEqual(self.get("third", f"/listings/{lid}").status_code, 404)
        self.assertEqual(self.report("asker", lid).status_code, 404)
        report = self.conn.execute("SELECT status FROM reports").fetchone()
        self.assertEqual(report["status"], "resolved")


class SimultaneousAcceptTests(StoryWorld, unittest.TestCase):
    """Two threads, each with its own connection to the same database file."""

    def run_together(self, jobs):
        barrier = threading.Barrier(len(jobs))
        results = [None] * len(jobs)

        def work(i, rid, user):
            conn = connect(self.app.config["DATABASE_PATH"])
            try:
                barrier.wait(timeout=10)
                results[i] = svc.act(conn, rid, "accept", user)
            except svc.ExchangeProblem as problem:
                results[i] = problem
            finally:
                conn.close()

        threads = [threading.Thread(target=work, args=(i, rid, user)) for i, (rid, user) in enumerate(jobs)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
            self.assertFalse(t.is_alive())
        return results

    def test_the_same_request_accepted_twice_wins_once(self):
        lid = self.listing(self.owner, "Book", mode="give")
        rid = self.ask("asker", lid)
        owner_row = self.user_row(self.owner)
        results = self.run_together([(rid, owner_row), (rid, owner_row)])
        wins = [r for r in results if r == "accepted"]
        refused = [r for r in results if isinstance(r, svc.ExchangeProblem)]
        self.assertEqual((len(wins), len(refused)), (1, 1), results)
        self.assertEqual(self.statuses(rid, lid), ("accepted", "reserved"))

    def test_two_requests_for_one_item_accepted_together_win_once(self):
        lid = self.listing(self.owner, "Book", mode="give")
        first, second = self.ask("asker", lid), self.ask("third", lid)
        owner_row = self.user_row(self.owner)
        results = self.run_together([(first, owner_row), (second, owner_row)])
        wins = [r for r in results if r == "accepted"]
        refused = [r for r in results if isinstance(r, svc.ExchangeProblem)]
        self.assertEqual((len(wins), len(refused)), (1, 1), results)
        accepted = self.conn.execute(
            "SELECT COUNT(*) FROM exchange_requests WHERE listing_id = ? AND status = 'accepted'",
            (lid,)).fetchone()[0]
        self.assertEqual(accepted, 1)
        self.assertEqual(self.lst(lid)["status"], "reserved")


class ConnectionSettingsTests(helpers.TempDirMixin, unittest.TestCase):
    def test_every_connection_has_wal_foreign_keys_and_a_busy_wait(self):
        path = self.make_tmp() + "/settings.db"
        conn = connect(path)
        self.addCleanup(conn.close)
        self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(conn.execute("PRAGMA busy_timeout").fetchone()[0], 5000)
        other = connect(path)                                   # a second connection gets them too
        self.addCleanup(other.close)
        self.assertEqual(other.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(other.execute("PRAGMA busy_timeout").fetchone()[0], 5000)


class PerfScriptTests(helpers.TempDirMixin, unittest.TestCase):
    def test_the_scratch_data_builder_makes_pages_that_answer(self):
        from scripts import perf
        app, users, listings = perf.build(self.make_tmp(), 12, 60)
        client = perf.client_for(app, users[0])
        for path in perf.pages_for(listings[0]):
            self.assertIn(client.get(path).status_code, (200, 303), path)


if __name__ == "__main__":
    unittest.main()
