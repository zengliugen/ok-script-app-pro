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

The repository is public and GitHub Pages is enabled (**Build and deployment → Source = GitHub Actions**). The site is published at:

```text
https://zengliugen.github.io/ok-script-app-pro/
```

`.github/workflows/docs.yml` builds and deploys on pushes to `master`, `main`, or `dev` that touch `docs/`, `mkdocs.yml`, `requirements-docs.txt`, or the workflow itself. It can also be run manually.

Validate locally:

```powershell
python -m mkdocs build --strict
```

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
