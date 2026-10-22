# Build the Documentation Site

Documentation sources live in `docs/`; navigation and site settings live in `mkdocs.yml`. Generated HTML is written to the ignored `site/` directory.

## Local Preview

Install documentation dependencies in the project virtual environment:

```powershell
python -m pip install --index-url https://pypi.org/simple/ -r requirements-docs.txt
python -m mkdocs serve
```

Open `http://127.0.0.1:8000/`. The development server rebuilds pages when Markdown changes.

## Build Static HTML

```powershell
python -m mkdocs build --strict
```

`--strict` treats navigation and internal-link warnings as build failures. Publish the contents of `site/` on any static host.

## GitHub Pages

**Current status: automatic deployment is disabled.**

This is a private repository, and free accounts cannot enable GitHub Pages for private repositories, so the `push` trigger in `.github/workflows/docs.yml` is commented out and only manual `workflow_dispatch` remains.

Validate documentation locally while it is disabled:

```powershell
python -m mkdocs build --strict
```

### Restoring automatic deployment

Restore it after either of the following:

1. Make the repository **public**, then set **Build and deployment → Source** to **GitHub Actions** under **Settings → Pages**.
2. Upgrade to a plan that supports Pages for private repositories and enable Pages.

Then uncomment the `push:` block in `.github/workflows/docs.yml` to build and deploy on pushes to `master`, `main`, or `dev`.

`site_name`, `site_description`, `repo_name`, `repo_url`, and `edit_uri` in `mkdocs.yml` already point at this project; update them if the repository or documentation branch changes.

## Layout

```text
mkdocs.yml                  MkDocs configuration and navigation
requirements-docs.txt      Documentation build dependencies
pyproject.toml             Source of truth for direct dependencies and profiles
docs/                       Chinese documentation
docs/en/                    English documentation
docs/images/                Shared images
.github/workflows/docs.yml  GitHub Pages build and deployment
site/                       Generated static HTML (ignored)
```

When adding a page, include it in `nav` in `mkdocs.yml` and run a strict build.
