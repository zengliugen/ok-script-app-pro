# ok-script-app-pro

English | [中文](README.md)

ok-script-app-pro is a capability-extension project built from the [ok-script-app](https://github.com/ok-oldking/ok-script-app) template and powered by [ok-script](https://github.com/ok-oldking/ok-script). It keeps the upstream template capabilities (native Windows games, Android emulators, and browser automation) and serves as the host for additional extensions developed on top of them.

The repository still follows the template layout; extensions are layered under `src/` over time. Task examples, OCR, template matching, configuration widgets, tests, i18n, EXE packaging, and update/release configuration remain available.

## Documentation

The complete documentation is organized as an MkDocs site:

- [Documentation home](docs/en/index.md)
- [Quick start](docs/en/getting-started.md)
- [App and runtime target configuration](docs/en/configuration.md)
- [Task development](docs/en/tasks.md)
- [Packaging and release](docs/en/release.md)
- [Build the documentation site](docs/en/documentation.md)
- [中文文档](docs/index.md)

## Quick Preview

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
$PypiIndex = "https://pypi.org/simple/"
python -m pip install --index-url $PypiIndex --upgrade pip
python -m pip install --index-url $PypiIndex --no-deps --upgrade -r requirements.txt
python main_debug.py
```

Direct dependencies are managed in `pyproject.toml`. `requirements.txt` is the
compiled Qt profile and `requirements-web.txt` is the compiled web profile; do
not edit these generated lock files directly. For the web profile, replace the
dependency and launch commands above with `requirements-web.txt` and
`python web_main_debug.py`, respectively. The official PyPI index URL for pip
must include `/simple/`; `https://pypi.org/` is not a valid package index URL.

## Build the Documentation Site

```powershell
python -m pip install --index-url https://pypi.org/simple/ -r requirements-docs.txt
python -m mkdocs serve
```

Open `http://127.0.0.1:8000/` to preview the site. Build static HTML with:

```powershell
python -m mkdocs build --strict
```

The output is written to `site/`. `.github/workflows/docs.yml` can publish it automatically to GitHub Pages; see the [documentation-site guide](docs/en/documentation.md) for setup.

## Relationship to Upstream

- Upstream template: [ok-oldking/ok-script-app](https://github.com/ok-oldking/ok-script-app)
- This project: extensions are developed on top of the template and track upstream through the `upstream` remote
- Release and updates: installers and the update source point at this repository

## Credits

- [ok-script-app](https://github.com/ok-oldking/ok-script-app)
- [ok-script](https://github.com/ok-oldking/ok-script)
- [OnnxOCR](https://github.com/ok-oldking/OnnxOCR)
- [PyQt-Fluent-Widgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets)
