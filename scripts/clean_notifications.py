#!/usr/bin/env python3
"""Mark GitHub notifications as done when their PR or issue is merged or closed.

Lists every notification in the inbox (read and unread), looks up the state of
each pull request or issue it points at, and marks the thread as done
(DELETE /notifications/threads/{id}) when that PR or issue is merged or closed.
Release notifications are always marked done. Other notification types
(discussions, check suites, ...) are left alone.

Environment:
  GITHUB_TOKEN  classic personal access token with the `notifications` and
                `repo` scopes. Fine-grained tokens and the Actions GITHUB_TOKEN
                cannot read a user's notifications.
  DRY_RUN       "true" to only log what would be marked done.

Uses only the Python standard library.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"
HANDLED_TYPES = {"PullRequest", "Issue"}
ALWAYS_DONE_TYPES = {"Release"}


class GitHub:
    def __init__(self, token):
        self.token = token

    def request(self, method, url):
        """Return (status, headers, parsed JSON or None)."""
        req = urllib.request.Request(url, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        req.add_header("User-Agent", "notification-cleanup")
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read()
            data = json.loads(body) if body else None
            return resp.status, resp.headers, data

    def paginate(self, url):
        while url:
            _, headers, data = self.request("GET", url)
            yield from data or []
            url = next_link(headers.get("Link", ""))


def next_link(link_header):
    match = re.search(r'<([^>]+)>;\s*rel="next"', link_header or "")
    return match.group(1) if match else None


def subject_is_finished(gh, subject, cache):
    """True if the PR/issue is merged or closed, False if open, None if unknown."""
    url = subject.get("url")
    if not url:
        return None
    if url not in cache:
        _, _, data = gh.request("GET", url)
        # Pull requests report state "closed" both when merged and when closed
        # unmerged, so the state field covers both cases for PRs and issues.
        cache[url] = (data or {}).get("state") == "closed" or bool((data or {}).get("merged"))
    return cache[url]


def run(gh, dry_run):
    counts = {"seen": 0, "done": 0, "open": 0, "skipped": 0, "errors": 0}
    cache = {}
    # all=true includes notifications already read but still in the inbox.
    # Fetch every page before marking anything done, so removing threads
    # doesn't shift later pages and skip notifications.
    threads = list(gh.paginate(f"{API}/notifications?all=true&per_page=50"))
    for thread in threads:
        counts["seen"] += 1
        subject = thread.get("subject") or {}
        kind = subject.get("type")
        repo = (thread.get("repository") or {}).get("full_name", "?")
        label = f"[{kind}] {repo}: {subject.get('title', '')}"

        if kind in ALWAYS_DONE_TYPES:
            finished = True
        elif kind not in HANDLED_TYPES:
            counts["skipped"] += 1
            continue
        else:
            try:
                finished = subject_is_finished(gh, subject, cache)
            except urllib.error.HTTPError as err:
                # 404/403/410: deleted, transferred or no longer accessible.
                print(f"  could not read {label}: HTTP {err.code}", file=sys.stderr)
                counts["errors"] += 1
                continue
            except (urllib.error.URLError, TimeoutError) as err:
                print(f"  could not read {label}: {err}", file=sys.stderr)
                counts["errors"] += 1
                continue

        if finished is None:
            counts["skipped"] += 1
            continue
        if not finished:
            counts["open"] += 1
            continue

        if dry_run:
            print(f"  would mark done: {label}")
            counts["done"] += 1
            continue
        try:
            gh.request("DELETE", f"{API}/notifications/threads/{thread['id']}")
            print(f"  marked done: {label}")
            counts["done"] += 1
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as err:
            print(f"  failed to mark done {label}: {err}", file=sys.stderr)
            counts["errors"] += 1
    return counts


def main():
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("GITHUB_TOKEN is not set (add the NOTIFICATIONS_TOKEN repo secret)", file=sys.stderr)
        return 1
    dry_run = os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")
    counts = run(GitHub(token), dry_run)
    verb = "Would mark" if dry_run else "Marked"
    print(
        f"{verb} {counts['done']} done. Checked {counts['seen']} notifications: "
        f"{counts['open']} still open, {counts['skipped']} other types skipped, "
        f"{counts['errors']} errors."
    )
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as fh:
            fh.write(
                f"### Notification cleanup{' (dry run)' if dry_run else ''}\n\n"
                f"| {verb} done | Still open | Other types | Errors | Total |\n"
                f"|---|---|---|---|---|\n"
                f"| {counts['done']} | {counts['open']} | {counts['skipped']} "
                f"| {counts['errors']} | {counts['seen']} |\n"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
