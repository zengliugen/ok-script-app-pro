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

Install one dependency profile for the app's runtime target. For the Qt desktop UI:

```powershell
python -m pip install --index-url $PypiIndex --no-deps --upgrade -r requirements.txt
python main_debug.py
```

For the web UI:

```powershell
python -m pip install --index-url $PypiIndex --no-deps --upgrade -r requirements-web.txt
python web_main_debug.py
```

The lock files provide reproducible installations and are recommended for daily
development. To resolve the latest compatible dependencies directly from
`pyproject.toml`, use the applicable command instead:

```powershell
python -m pip install --index-url $PypiIndex ".[qt]"
python -m pip install --index-url $PypiIndex ".[web]"
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
