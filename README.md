# git-pr-extractor

Extract any GitHub Pull Request as a **complete, standalone repository snapshot** and download it as a ready-to-use ZIP file — no local git knowledge required.  Works on Windows, macOS, and Linux.

---

## Features

- Simple graphical interface (Windows-native feel via **Tkinter**)
- Paste any GitHub PR URL and click **Extract PR**
- Clones the PR's base branch, applies the PR changes on top, then zips the result
- Supports private repositories via a GitHub Personal Access Token
- **Shallow clone** option for large repositories (faster download)
- No manual git operations needed after the ZIP is created

---

## Requirements

| Requirement | Notes |
|---|---|
| Python 3.8+ | Bundled with Tkinter on Windows |
| `git` CLI | Must be on your `PATH` |
| `requests` library | `pip install requests` |

Install Python dependencies:

```
pip install -r requirements.txt
```

---

## Usage

### Graphical Interface (recommended)

```
python extractor.py
```

1. Paste the full GitHub PR URL into the **PR URL** field.  
   Example: `https://github.com/citizenfx/fivem/pull/3477`
2. *(Optional)* Enter a GitHub Personal Access Token to avoid API rate limits or to access private repositories.
3. Choose an output directory where the ZIP will be saved.
4. Check or uncheck **Shallow clone** as appropriate (recommended for large repositories).
5. Click **Extract PR** and wait for the log to report completion.
6. The resulting ZIP (`<repo>_PR<number>.zip`) contains the full source tree with the PR already merged.

### Command-line / scripting

```python
from extractor import extract_pr

zip_path = extract_pr(
    pr_url="https://github.com/citizenfx/fivem/pull/3477",
    output_dir="C:/Downloads",
    token="ghp_...",   # optional
    shallow=True,      # faster for large repos
)
print(f"ZIP saved to: {zip_path}")
```

---

## What the ZIP contains

- The **complete source tree** of the repository at the state where the PR is merged into its target branch.
- No `.git` directory — it is a clean snapshot, not a live repository.
- Can be extracted and used immediately as a build directory.

---

## Running the tests

```
pip install pytest requests
python -m pytest test_extractor.py -v
```

---

## Notes for large repositories (e.g. citizenfx/fivem)

The fivem repository is several gigabytes with many submodules.  
- Enable **Shallow clone** to speed up the initial download.  
- Provide a GitHub token to avoid hitting the unauthenticated API rate limit.  
- The extraction may still take several minutes depending on your internet connection.

