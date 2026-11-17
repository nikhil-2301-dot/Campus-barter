"""C17 / review item R7: the browse search text is capped, so a huge value cannot break the page.
(Keep the words error / failed / skipped out of names and docstrings: scripts/check.sh gates on them.)"""
import unittest

import test_browse_ui as base
from app import listings
from test_browse_ui import page


class SearchCapTests(base.BrowseCase):
    def test_huge_search_text_still_shows_the_page(self):
        r = self.app.test_client().get("/listings/?q=" + "a" * 50000)
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("could not be loaded", r.get_data(as_text=True))

    def test_search_text_is_cut_to_the_cap_and_shown_cut(self):
        r = self.app.test_client().get("/listings/?q=" + "b" * 300)
        box = page(r.get_data(as_text=True)).find("input", id="f-q")
        self.assertEqual(len(box["value"]), listings.MAX_SEARCH_LEN)

    def test_search_box_is_a_search_input_with_the_same_limit(self):
        box = page(self.app.test_client().get("/listings/").get_data(as_text=True)).find("input", id="f-q")
        self.assertEqual(box["type"], "search")
        self.assertEqual(int(box["maxlength"]), listings.MAX_SEARCH_LEN)

    def test_text_inside_the_cap_is_untouched(self):
        r = self.app.test_client().get("/listings/?q=drafter")
        self.assertEqual(page(r.get_data(as_text=True)).find("input", id="f-q")["value"], "drafter")


if __name__ == "__main__":
    unittest.main()
