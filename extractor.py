"""
GitHub PR Extractor
Extract a GitHub Pull Request as a complete, standalone repository snapshot
and package it as a ZIP file ready for download on Windows.
"""

import os
import re
import sys
import json
import shutil
import zipfile
import threading
import subprocess
import tempfile
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

try:
    import requests
except ImportError:
    requests = None


# ---------------------------------------------------------------------------
# Core extraction logic (no UI dependency)
# ---------------------------------------------------------------------------

def parse_pr_url(url: str):
    """
    Parse a GitHub Pull Request URL and return (owner, repo, pr_number).

    Supported formats:
      https://github.com/owner/repo/pull/123
      https://github.com/owner/repo/pull/123/files  (and similar sub-paths)
    """
    url = url.strip().rstrip("/")
    pattern = r"https?://github\.com/([^/]+)/([^/]+)/pull/(\d+)"
    m = re.search(pattern, url)
    if not m:
        raise ValueError(
            f"Cannot parse PR URL: {url!r}\n"
            "Expected format: https://github.com/owner/repo/pull/NUMBER"
        )
    owner, repo, pr_number = m.group(1), m.group(2), int(m.group(3))
    return owner, repo, pr_number


def fetch_pr_info(owner: str, repo: str, pr_number: int, token: str = ""):
    """
    Fetch PR metadata from the GitHub REST API.
    Returns a dict with at least: 'base_ref', 'head_sha', 'title', 'state'.
    """
    if requests is None:
        raise RuntimeError(
            "'requests' library is not installed.\n"
            "Run: pip install requests"
        )
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    response = requests.get(url, headers=headers, timeout=30)
    if response.status_code == 404:
        raise ValueError(
            f"PR #{pr_number} not found in {owner}/{repo}.\n"
            "Check the URL and ensure the repository is public (or provide a token)."
        )
    if response.status_code == 403:
        raise ValueError(
            "GitHub API rate limit exceeded or authentication required.\n"
            "Provide a GitHub personal access token in the Token field."
        )
    response.raise_for_status()
    data = response.json()
    return {
        "title": data["title"],
        "state": data["state"],
        "base_ref": data["base"]["ref"],
        "base_sha": data["base"]["sha"],
        "head_sha": data["head"]["sha"],
        "head_ref": data["head"]["ref"],
        "clone_url": data["base"]["repo"]["clone_url"],
        "repo_full_name": data["base"]["repo"]["full_name"],
    }


def _run(cmd, cwd=None, env=None, log=None):
    """Run a subprocess command, streaming output to *log* callable if given."""
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    output_lines = []
    for line in proc.stdout:
        line = line.rstrip()
        output_lines.append(line)
        if log:
            log(line)
    proc.wait()
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(
            proc.returncode, cmd, output="\n".join(output_lines)
        )
    return "\n".join(output_lines)


def extract_pr(
    pr_url: str,
    output_dir: str,
    token: str = "",
    log=None,
    shallow: bool = True,
):
    """
    Clone the base branch of the PR's repository, apply the PR changes on top,
    then zip the result into *output_dir*.

    Parameters
    ----------
    pr_url    : Full GitHub PR URL.
    output_dir: Directory where the final ZIP will be saved.
    token     : Optional GitHub personal access token (for private repos / rate limits).
    log       : Optional callable(str) for progress messages.
    shallow   : Use --depth 1 clone for speed (recommended for large repos).

    Returns
    -------
    str  Path to the created ZIP file.
    """
    if log is None:
        log = print

    log(f"Parsing PR URL: {pr_url}")
    owner, repo, pr_number = parse_pr_url(pr_url)
    log(f"Repository : {owner}/{repo}")
    log(f"PR number  : #{pr_number}")

    log("Fetching PR metadata from GitHub API …")
    info = fetch_pr_info(owner, repo, pr_number, token=token)
    log(f"PR title   : {info['title']}")
    log(f"Base branch: {info['base_ref']}")
    log(f"PR state   : {info['state']}")

    # Build an authenticated clone URL when a token is provided
    clone_url = info["clone_url"]
    if token:
        clone_url = clone_url.replace(
            "https://", f"https://oauth2:{token}@"
        )

    work_dir = tempfile.mkdtemp(prefix="pr_extractor_")
    repo_dir = os.path.join(work_dir, repo)
    try:
        # ------------------------------------------------------------------
        # 1. Clone the base branch
        # ------------------------------------------------------------------
        log(f"\nCloning {info['repo_full_name']} (branch: {info['base_ref']}) …")
        log("This may take a while for large repositories.")
        clone_cmd = [
            "git", "clone",
            "--branch", info["base_ref"],
            "--single-branch",
        ]
        if shallow:
            clone_cmd += ["--depth", "1"]
        clone_cmd += [clone_url, repo_dir]
        _run(clone_cmd, log=log)

        # ------------------------------------------------------------------
        # 2. Fetch the PR head ref
        # ------------------------------------------------------------------
        log(f"\nFetching PR #{pr_number} changes …")
        fetch_ref = f"pull/{pr_number}/head:pr-{pr_number}"
        fetch_cmd = ["git", "fetch"]
        if shallow:
            fetch_cmd += ["--depth", "1"]
        fetch_cmd += ["origin", fetch_ref]
        _run(fetch_cmd, cwd=repo_dir, log=log)

        # ------------------------------------------------------------------
        # 3. Merge the PR branch into the base branch
        # ------------------------------------------------------------------
        log(f"\nMerging PR #{pr_number} into {info['base_ref']} …")
        env = os.environ.copy()
        env["GIT_AUTHOR_NAME"] = "PR Extractor"
        env["GIT_AUTHOR_EMAIL"] = "pr-extractor@localhost"
        env["GIT_COMMITTER_NAME"] = "PR Extractor"
        env["GIT_COMMITTER_EMAIL"] = "pr-extractor@localhost"
        merge_cmd = ["git", "merge", f"pr-{pr_number}", "--no-edit",
                     "-m", f"Merge PR #{pr_number}: {info['title']}"]
        if shallow:
            # With shallow clones the common ancestor may not be present
            merge_cmd.append("--allow-unrelated-histories")
        _run(merge_cmd, cwd=repo_dir, env=env, log=log)

        # ------------------------------------------------------------------
        # 4. Remove the .git directory (optional clean build snapshot)
        # ------------------------------------------------------------------
        git_folder = os.path.join(repo_dir, ".git")
        if os.path.isdir(git_folder):
            shutil.rmtree(git_folder)

        # ------------------------------------------------------------------
        # 5. Package as ZIP
        # ------------------------------------------------------------------
        os.makedirs(output_dir, exist_ok=True)
        safe_repo = re.sub(r"[^A-Za-z0-9_.-]", "_", repo)
        zip_name = f"{safe_repo}_PR{pr_number}.zip"
        zip_path = os.path.join(output_dir, zip_name)

        log(f"\nPackaging repository into {zip_path} …")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for dirpath, dirnames, filenames in os.walk(repo_dir):
                # Skip hidden directories (e.g. .git if somehow still present)
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                for filename in filenames:
                    full_path = os.path.join(dirpath, filename)
                    arcname = os.path.relpath(full_path, start=work_dir)
                    zf.write(full_path, arcname)

        log(f"\nDone!  ZIP file created:")
        log(f"  {zip_path}")
        return zip_path

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Tkinter GUI
# ---------------------------------------------------------------------------

class PRExtractorApp:
    """Main application window."""

    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("GitHub PR Extractor")
        root.resizable(True, True)
        root.minsize(640, 480)
        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        pad = {"padx": 10, "pady": 5}

        # ---- Input frame -----------------------------------------------
        frame_in = ttk.LabelFrame(self.root, text="Pull Request", padding=10)
        frame_in.pack(fill=tk.X, **pad)

        ttk.Label(frame_in, text="PR URL:").grid(row=0, column=0, sticky=tk.W)
        self.var_url = tk.StringVar()
        ttk.Entry(frame_in, textvariable=self.var_url, width=70).grid(
            row=0, column=1, columnspan=2, sticky=tk.EW, padx=(6, 0)
        )

        ttk.Label(frame_in, text="GitHub Token (optional):").grid(
            row=1, column=0, sticky=tk.W, pady=(6, 0)
        )
        self.var_token = tk.StringVar()
        ttk.Entry(frame_in, textvariable=self.var_token, show="*", width=70).grid(
            row=1, column=1, columnspan=2, sticky=tk.EW, padx=(6, 0), pady=(6, 0)
        )

        frame_in.columnconfigure(1, weight=1)

        # ---- Output frame ----------------------------------------------
        frame_out = ttk.LabelFrame(self.root, text="Output", padding=10)
        frame_out.pack(fill=tk.X, **pad)

        ttk.Label(frame_out, text="Save ZIP to:").grid(row=0, column=0, sticky=tk.W)
        self.var_output = tk.StringVar(
            value=os.path.join(os.path.expanduser("~"), "Downloads")
        )
        ttk.Entry(frame_out, textvariable=self.var_output, width=60).grid(
            row=0, column=1, sticky=tk.EW, padx=(6, 0)
        )
        ttk.Button(frame_out, text="Browse…", command=self._browse_output).grid(
            row=0, column=2, padx=(6, 0)
        )

        self.var_shallow = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            frame_out,
            text="Shallow clone (faster, recommended for large repositories)",
            variable=self.var_shallow,
        ).grid(row=1, column=0, columnspan=3, sticky=tk.W, pady=(6, 0))

        frame_out.columnconfigure(1, weight=1)

        # ---- Action button ---------------------------------------------
        self.btn_extract = ttk.Button(
            self.root, text="Extract PR", command=self._start_extraction
        )
        self.btn_extract.pack(pady=(4, 0))

        # ---- Log frame -------------------------------------------------
        frame_log = ttk.LabelFrame(self.root, text="Log", padding=10)
        frame_log.pack(fill=tk.BOTH, expand=True, **pad)

        self.log_box = scrolledtext.ScrolledText(
            frame_log, state=tk.DISABLED, height=15, font=("Courier New", 9)
        )
        self.log_box.pack(fill=tk.BOTH, expand=True)

        # ---- Status bar ------------------------------------------------
        self.var_status = tk.StringVar(value="Ready.")
        ttk.Label(self.root, textvariable=self.var_status, relief=tk.SUNKEN).pack(
            fill=tk.X, side=tk.BOTTOM, ipady=2
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _browse_output(self):
        path = filedialog.askdirectory(
            title="Select output directory",
            initialdir=self.var_output.get() or os.path.expanduser("~"),
        )
        if path:
            self.var_output.set(path)

    def _log(self, message: str):
        """Append a line to the log box (thread-safe via root.after)."""
        def _append():
            self.log_box.configure(state=tk.NORMAL)
            self.log_box.insert(tk.END, message + "\n")
            self.log_box.see(tk.END)
            self.log_box.configure(state=tk.DISABLED)

        self.root.after(0, _append)

    def _set_status(self, text: str):
        self.root.after(0, lambda: self.var_status.set(text))

    def _set_busy(self, busy: bool):
        state = tk.DISABLED if busy else tk.NORMAL
        self.root.after(0, lambda: self.btn_extract.configure(state=state))

    # ------------------------------------------------------------------
    # Extraction workflow
    # ------------------------------------------------------------------

    def _start_extraction(self):
        url = self.var_url.get().strip()
        if not url:
            messagebox.showwarning("Missing URL", "Please enter a GitHub PR URL.")
            return

        output_dir = self.var_output.get().strip()
        if not output_dir:
            messagebox.showwarning("Missing output", "Please select an output directory.")
            return

        # Clear log
        self.log_box.configure(state=tk.NORMAL)
        self.log_box.delete("1.0", tk.END)
        self.log_box.configure(state=tk.DISABLED)

        self._set_busy(True)
        self._set_status("Extracting PR …")

        thread = threading.Thread(
            target=self._extraction_worker,
            args=(url, output_dir, self.var_token.get().strip(), self.var_shallow.get()),
            daemon=True,
        )
        thread.start()

    def _extraction_worker(self, url, output_dir, token, shallow):
        try:
            zip_path = extract_pr(
                pr_url=url,
                output_dir=output_dir,
                token=token,
                log=self._log,
                shallow=shallow,
            )
            self._set_status(f"Success — {os.path.basename(zip_path)}")
            self.root.after(
                0,
                lambda: messagebox.showinfo(
                    "Extraction complete",
                    f"ZIP file saved to:\n{zip_path}",
                ),
            )
        except Exception as exc:  # pylint: disable=broad-except
            self._log(f"\nERROR: {exc}")
            self._set_status("Extraction failed.")
            self.root.after(
                0,
                lambda: messagebox.showerror("Extraction failed", str(exc)),
            )
        finally:
            self._set_busy(False)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    root = tk.Tk()
    PRExtractorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
