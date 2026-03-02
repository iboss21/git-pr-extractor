"""
Tests for the core extraction logic in extractor.py.
Run with:  python -m pytest test_extractor.py -v
"""

import os
import sys
import json
import subprocess
import zipfile
import tempfile
import unittest
from unittest.mock import patch, MagicMock, call

# Import the module under test (avoid launching the Tk UI at import time).
sys.modules.setdefault("tkinter", MagicMock())
import extractor  # noqa: E402


# ---------------------------------------------------------------------------
# parse_pr_url
# ---------------------------------------------------------------------------

class TestParsePrUrl(unittest.TestCase):

    def test_standard_url(self):
        owner, repo, num = extractor.parse_pr_url(
            "https://github.com/citizenfx/fivem/pull/3477"
        )
        self.assertEqual(owner, "citizenfx")
        self.assertEqual(repo, "fivem")
        self.assertEqual(num, 3477)

    def test_url_with_trailing_slash(self):
        owner, repo, num = extractor.parse_pr_url(
            "https://github.com/owner/my-repo/pull/99/"
        )
        self.assertEqual(owner, "owner")
        self.assertEqual(repo, "my-repo")
        self.assertEqual(num, 99)

    def test_url_with_sub_path(self):
        owner, repo, num = extractor.parse_pr_url(
            "https://github.com/owner/repo/pull/42/files"
        )
        self.assertEqual(num, 42)

    def test_invalid_url_raises(self):
        with self.assertRaises(ValueError):
            extractor.parse_pr_url("https://github.com/owner/repo")

    def test_invalid_url_no_number(self):
        with self.assertRaises(ValueError):
            extractor.parse_pr_url("https://github.com/owner/repo/pull/")

    def test_http_scheme(self):
        owner, repo, num = extractor.parse_pr_url(
            "http://github.com/org/project/pull/1"
        )
        self.assertEqual(owner, "org")
        self.assertEqual(num, 1)


# ---------------------------------------------------------------------------
# fetch_pr_info
# ---------------------------------------------------------------------------

MOCK_API_RESPONSE = {
    "title": "Test PR",
    "state": "open",
    "base": {
        "ref": "master",
        "sha": "abc123",
        "repo": {
            "clone_url": "https://github.com/owner/repo.git",
            "full_name": "owner/repo",
        },
    },
    "head": {
        "ref": "feature-branch",
        "sha": "def456",
    },
}


class TestFetchPrInfo(unittest.TestCase):

    @patch("extractor.requests")
    def test_successful_fetch(self, mock_requests):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = MOCK_API_RESPONSE
        mock_requests.get.return_value = mock_resp

        info = extractor.fetch_pr_info("owner", "repo", 1)

        self.assertEqual(info["title"], "Test PR")
        self.assertEqual(info["base_ref"], "master")
        self.assertEqual(info["clone_url"], "https://github.com/owner/repo.git")

    @patch("extractor.requests")
    def test_404_raises_value_error(self, mock_requests):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_requests.get.return_value = mock_resp

        with self.assertRaises(ValueError) as ctx:
            extractor.fetch_pr_info("owner", "repo", 999)
        self.assertIn("not found", str(ctx.exception))

    @patch("extractor.requests")
    def test_403_raises_value_error(self, mock_requests):
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_requests.get.return_value = mock_resp

        with self.assertRaises(ValueError) as ctx:
            extractor.fetch_pr_info("owner", "repo", 1)
        self.assertIn("rate limit", str(ctx.exception).lower())

    @patch("extractor.requests")
    def test_token_sent_in_header(self, mock_requests):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = MOCK_API_RESPONSE
        mock_requests.get.return_value = mock_resp

        extractor.fetch_pr_info("owner", "repo", 1, token="my_secret_token")

        _, kwargs = mock_requests.get.call_args
        headers = kwargs.get("headers", {})
        self.assertIn("Authorization", headers)
        self.assertIn("my_secret_token", headers["Authorization"])

    def test_no_requests_raises_runtime_error(self):
        original = extractor.requests
        extractor.requests = None
        try:
            with self.assertRaises(RuntimeError):
                extractor.fetch_pr_info("owner", "repo", 1)
        finally:
            extractor.requests = original


# ---------------------------------------------------------------------------
# extract_pr  (integration-style test using a local git repo)
# ---------------------------------------------------------------------------

def _init_local_repo(path: str, branch: str = "main") -> str:
    """Create a minimal git repository at *path* and return the path."""
    env = os.environ.copy()
    env["GIT_AUTHOR_NAME"] = "Test"
    env["GIT_AUTHOR_EMAIL"] = "test@test.com"
    env["GIT_COMMITTER_NAME"] = "Test"
    env["GIT_COMMITTER_EMAIL"] = "test@test.com"

    subprocess.check_call(["git", "init", "-b", branch, path], env=env)
    readme = os.path.join(path, "README.md")
    with open(readme, "w") as f:
        f.write("base content\n")
    subprocess.check_call(["git", "add", "."], cwd=path, env=env)
    subprocess.check_call(
        ["git", "commit", "-m", "Initial commit"], cwd=path, env=env
    )
    return path


def _add_pr_branch(repo_path: str, pr_number: int, base_branch: str = "main"):
    """Add a PR-like branch to the local repo and push refs/pull/<n>/head."""
    env = os.environ.copy()
    env["GIT_AUTHOR_NAME"] = "Test"
    env["GIT_AUTHOR_EMAIL"] = "test@test.com"
    env["GIT_COMMITTER_NAME"] = "Test"
    env["GIT_COMMITTER_EMAIL"] = "test@test.com"

    branch = f"pr-branch-{pr_number}"
    subprocess.check_call(
        ["git", "checkout", "-b", branch], cwd=repo_path, env=env
    )
    new_file = os.path.join(repo_path, f"pr_{pr_number}.txt")
    with open(new_file, "w") as f:
        f.write(f"PR {pr_number} change\n")
    subprocess.check_call(["git", "add", "."], cwd=repo_path, env=env)
    subprocess.check_call(
        ["git", "commit", "-m", f"PR {pr_number} commit"], cwd=repo_path, env=env
    )
    # Create the refs/pull/<n>/head ref so `git fetch origin pull/n/head:...` works
    subprocess.check_call(
        ["git", "update-ref", f"refs/pull/{pr_number}/head", "HEAD"],
        cwd=repo_path,
        env=env,
    )
    subprocess.check_call(
        ["git", "checkout", base_branch], cwd=repo_path, env=env
    )


class TestExtractPr(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_local_repos(self):
        """Return (origin_path, output_dir)."""
        origin = os.path.join(self.tmp, "origin")
        _init_local_repo(origin, branch="main")
        _add_pr_branch(origin, pr_number=1)
        output_dir = os.path.join(self.tmp, "output")
        return origin, output_dir

    @patch("extractor.fetch_pr_info")
    def test_extract_creates_zip(self, mock_fetch):
        origin, output_dir = self._make_local_repos()
        mock_fetch.return_value = {
            "title": "Test PR",
            "state": "open",
            "base_ref": "main",
            "base_sha": "abc",
            "head_sha": "def",
            "head_ref": "pr-branch-1",
            "clone_url": origin,
            "repo_full_name": "owner/origin",
        }

        zip_path = extractor.extract_pr(
            pr_url="https://github.com/owner/origin/pull/1",
            output_dir=output_dir,
            shallow=False,
        )

        self.assertTrue(os.path.isfile(zip_path))
        self.assertTrue(zip_path.endswith(".zip"))

    @patch("extractor.fetch_pr_info")
    def test_zip_contains_pr_file(self, mock_fetch):
        origin, output_dir = self._make_local_repos()
        mock_fetch.return_value = {
            "title": "Test PR",
            "state": "open",
            "base_ref": "main",
            "base_sha": "abc",
            "head_sha": "def",
            "head_ref": "pr-branch-1",
            "clone_url": origin,
            "repo_full_name": "owner/origin",
        }

        zip_path = extractor.extract_pr(
            pr_url="https://github.com/owner/origin/pull/1",
            output_dir=output_dir,
            shallow=False,
        )

        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
        # The PR file and the base README should both be present
        self.assertTrue(any("pr_1.txt" in n for n in names), f"pr_1.txt not in {names}")
        self.assertTrue(any("README.md" in n for n in names), f"README.md not in {names}")

    @patch("extractor.fetch_pr_info")
    def test_zip_does_not_contain_git_dir(self, mock_fetch):
        origin, output_dir = self._make_local_repos()
        mock_fetch.return_value = {
            "title": "Test PR",
            "state": "open",
            "base_ref": "main",
            "base_sha": "abc",
            "head_sha": "def",
            "head_ref": "pr-branch-1",
            "clone_url": origin,
            "repo_full_name": "owner/origin",
        }

        zip_path = extractor.extract_pr(
            pr_url="https://github.com/owner/origin/pull/1",
            output_dir=output_dir,
            shallow=False,
        )

        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
        self.assertFalse(
            any("/.git/" in n or n.startswith(".git/") for n in names),
            f".git entries found in zip: {[n for n in names if '.git' in n]}",
        )

    @patch("extractor.fetch_pr_info")
    def test_invalid_url_raises(self, mock_fetch):
        with self.assertRaises(ValueError):
            extractor.extract_pr(
                pr_url="not-a-url",
                output_dir=self.tmp,
            )
        mock_fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
