import os, re, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


class ReleaseDocsTests(unittest.TestCase):
    def test_review_two_rows_each_have_an_id_severity_and_status(self):
        text = read("docs", "security", "review-2.md")
        rows = re.findall(r"(?m)^\| (R\d+) \| (HIGH|MEDIUM|LOW|NOTE) \|.*\| (FIXED|OPEN|NOTE|ACCEPTED)\b", text)
        self.assertGreaterEqual(len(rows), 5)
        self.assertEqual(len({r[0] for r in rows}), len(rows))

    def test_no_high_finding_is_open_in_either_review(self):
        for name in ("review-1.md", "review-2.md"):
            text = read("docs", "security", name)
            self.assertNotRegex(text, r"(?m)^\| R\d+ \| HIGH \|.*\| OPEN", name)

    def test_review_two_covers_every_review_one_id(self):
        first = set(re.findall(r"(?m)^\| (R\d+) \|", read("docs", "security", "review-1.md")))
        second = read("docs", "security", "review-2.md")
        for i in sorted(first):
            self.assertRegex(second, rf"(?m)^\| {i} ", i)


if __name__ == "__main__":
    unittest.main()
