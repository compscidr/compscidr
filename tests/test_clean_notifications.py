"""Smoke tests for scripts/clean_notifications.py against a fake GitHub API."""

import io
import os
import sys
import unittest
import unittest.mock
import urllib.error
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import clean_notifications as cn  # noqa: E402


def thread(tid, kind, url=None):
    subject = {"type": kind, "title": f"{kind} {tid}"}
    if url:
        subject["url"] = url
    return {"id": tid, "subject": subject, "repository": {"full_name": "o/r"}}


class FakeGitHub:
    """Serves a fixed inbox and subject states; records DELETEs."""

    def __init__(self, threads, subjects):
        self.threads = threads
        self.subjects = subjects  # url -> JSON dict, or int HTTP error code
        self.deleted = []
        self.gets = []

    def paginate(self, url):
        assert "all=true" in url
        return list(self.threads)

    def request(self, method, url):
        if method == "DELETE":
            self.deleted.append(url.rsplit("/", 1)[1])
            return 204, {}, None
        self.gets.append(url)
        result = self.subjects[url]
        if isinstance(result, int):
            raise urllib.error.HTTPError(url, result, "error", {}, None)
        return 200, {}, result


INBOX = [
    thread("1", "PullRequest", "api/pr/merged"),
    thread("2", "PullRequest", "api/pr/closed"),
    thread("3", "PullRequest", "api/pr/open"),
    thread("4", "Issue", "api/issue/closed"),
    thread("5", "Issue", "api/issue/open"),
    thread("6", "Issue", "api/issue/gone"),
    thread("7", "CheckSuite"),
    thread("8", "Discussion", "api/discussion"),
]
SUBJECTS = {
    "api/pr/merged": {"state": "closed", "merged": True},
    "api/pr/closed": {"state": "closed", "merged": False},
    "api/pr/open": {"state": "open", "merged": False},
    "api/issue/closed": {"state": "closed"},
    "api/issue/open": {"state": "open"},
    "api/issue/gone": 404,
}


def quiet_run(gh, dry_run):
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        return cn.run(gh, dry_run)


class RunTest(unittest.TestCase):
    def test_marks_merged_and_closed_done_and_keeps_the_rest(self):
        gh = FakeGitHub(INBOX, SUBJECTS)
        counts = quiet_run(gh, dry_run=False)
        self.assertEqual(sorted(gh.deleted), ["1", "2", "4"])
        self.assertEqual(
            counts, {"seen": 8, "done": 3, "open": 2, "skipped": 2, "errors": 1}
        )
        self.assertNotIn("api/discussion", gh.gets)

    def test_dry_run_marks_nothing(self):
        gh = FakeGitHub(INBOX, SUBJECTS)
        counts = quiet_run(gh, dry_run=True)
        self.assertEqual(gh.deleted, [])
        self.assertEqual(counts["done"], 3)

    def test_subject_state_is_fetched_once_per_url(self):
        inbox = [thread("1", "PullRequest", "api/pr/merged"),
                 thread("2", "PullRequest", "api/pr/merged")]
        gh = FakeGitHub(inbox, SUBJECTS)
        quiet_run(gh, dry_run=False)
        self.assertEqual(gh.gets, ["api/pr/merged"])
        self.assertEqual(sorted(gh.deleted), ["1", "2"])


class NextLinkTest(unittest.TestCase):
    def test_finds_next_page(self):
        header = ('<https://api.github.com/notifications?page=2>; rel="next", '
                  '<https://api.github.com/notifications?page=5>; rel="last"')
        self.assertEqual(cn.next_link(header), "https://api.github.com/notifications?page=2")

    def test_no_next_page(self):
        self.assertIsNone(cn.next_link('<https://x?page=1>; rel="prev"'))
        self.assertIsNone(cn.next_link(""))


class MainTest(unittest.TestCase):
    def test_missing_token_fails(self):
        env = {k: v for k, v in os.environ.items() if k != "GITHUB_TOKEN"}
        with unittest.mock.patch.dict(os.environ, env, clear=True), \
                redirect_stderr(io.StringIO()):
            self.assertEqual(cn.main(), 1)


if __name__ == "__main__":
    unittest.main()
