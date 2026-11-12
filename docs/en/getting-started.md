# Quick Start

## 1. Get the Repository

This project is derived from the upstream template and is already initialized as an extension project. Clone it directly:

```bash
git clone https://github.com/zengliugen/ok-script-app-pro.git
cd ok-script-app-pro
```

Two remotes are configured:

```bash
git remote -v
# origin   https://github.com/zengliugen/ok-script-app-pro.git
# upstream https://github.com/ok-oldking/ok-script-app.git
```

Extensions are developed on top of the template. To follow upstream:

```bash
git fetch upstream
git merge upstream/master
```

After merging, re-check `src/config.py`, `pyappify.yml`, and `.github/workflows` so upstream changes do not overwrite this project's identity and release configuration.

## 2. Install Python 3.12 and Project Dependencies

Install [Python 3.12.10](https://www.python.org/downloads/release/python-31210/), then run:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
$PypiIndex = "https://pypi.org/simple/"
python -m pip install --index-url $PypiIndex --upgrade pip
```

Install the Qt desktop UI dependencies (ok-script `3.0.0b3`):

```powershell
python -m pip install --index-url $PypiIndex --no-deps --upgrade -r requirements.txt
python main_debug.py
```

ok-script 3 removed the web frontend. The old web launchers, `web` extra, and
`requirements-web.txt` remain compatibility aliases for the desktop app.

The lock files provide reproducible installations and are recommended for daily
development. To resolve the latest compatible dependencies directly from
`pyproject.toml`, use the applicable command instead:

```powershell
python -m pip install --index-url $PypiIndex ".[qt]"
```

Run only the command for your target. The official PyPI index endpoint for pip is
`https://pypi.org/simple/`, not the website root `https://pypi.org/`. Using the
website root can report `No matching distribution found` even when the package
exists.

Administrator privileges are normally unnecessary. If the target game runs as administrator, launch the automation app at the same privilege level or capture and input may not work.

## 3. Confirm the App Configuration

The project identity is initialized as `ok-script-app-pro` and all three runtime target structures (Windows, Android emulator, browser) are retained. While developing extensions:

1. Confirm the application identity, runtime targets, icons, and update repository in [App configuration](configuration.md).
2. Add and register extension tasks with [Task development](tasks.md).
3. Start Debug mode:

```powershell
python main_debug.py
```

4. Run tests:

```powershell
python -m unittest tests.TestMain
```

5. After validation, configure the workflows and push a tag using [Packaging and release](release.md).

## 4. Handling Template Assets (required for downstream apps)

This section applies to **downstream applications built from this template**. The template repository itself keeps `ok_templates/` untracked; downstream apps must commit it.

### Two directories with different natures

| Directory | Contents | Regenerable | Commit downstream |
|---|---|---|---|
| `ok_templates/` | Original screenshots + hand-made annotations `coco_annotations.json` | **No** | **Required** |
| `assets/templates/` | Feature crops produced from annotations (Split mode) | Yes, from source images | Recommended |

`ok_templates/` is the editing area: you open the Template tab, draw boxes on a full screenshot, and name features. `assets/templates/` is the output area: the crops generated after clicking Save, loaded at runtime through `config['template_matching']['template_folder']`.

### Why downstream apps must commit `ok_templates/`

- **Annotations are not regenerable.** Every category name and bbox in `coco_annotations.json` is the result of manual work.
- **Screenshots are not regenerable.** `generate_templates` reads the source screenshot to produce crops and raises `ValueError` when it is missing (`ok/feature/FeatureSet.py`). Without committed sources, no one can re-export features.
- **Crops can be discarded.** Every PNG in `assets/templates/` can be regenerated from the source image plus annotation, so leaving them out costs only convenience.

### How to commit

Delete the entire `ok_templates/` line from `.gitignore`.

Do **not** rewrite it as `ok_templates/*` plus a `!ok_templates/xxx` negation — a directory-level ignore stops git from descending into the directory, so the negation has no effect:

```gitignore
ok_templates/
!ok_templates/coco_annotations.json    # ineffective: the file stays ignored
```

### Configure release filtering after committing

Committing `ok_templates/` serves collaboration, not distribution. End users do not need the source screenshots, and their size is substantial (hundreds of MB for mature projects).

**Critical: PyAppify does not read `.gitignore`.** It clones the full tree of the target tag with libgit2 and packages it. `gitignore` only affects `git add`, not the checkout of already-committed content. Its Rust sources contain no ignore-rule handling at all; the only exclusion in the whole pipeline is `__pycache__`.

Downstream apps therefore must do two things before releasing:

1. **Create a dedicated update repository** and sync into it through `ok-oldking/partial-sync-repo` with the `deploy.txt` whitelist in `build.yml`;
2. **Point every profile's `git_url` in `pyappify.yml` at the update repository**, not the source repository.

The whitelist is a positive enumeration: anything absent from `deploy.txt` never reaches the update repository. Use `.update_repo_gitignore` to cover build outputs and logs as well.

**Do not store these screenshots in Git LFS.** PyAppify goes through libgit2, which does not run the LFS smudge filter: CI with `lfs: true` gets the real images, but an end-user clone receives ~130-byte pointer text files that `cv2.imread` cannot decode. The failure appears only on user machines and is silent during the build.

See [Packaging and release](release.md) for details.
