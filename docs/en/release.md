# Packaging and Release

## Release Files

- `.github/workflows/build.yml`: watches `v*` tags, tests, syncs updates, packages, and creates a GitHub Release.
- `pyappify.yml`: defines the app name, entry point, icon, Python version, and update repositories.
- `pyproject.toml`: defines the Qt and documentation dependency profiles; `web` is a desktop compatibility alias.
- `requirements.txt`: compiled installation lock; `requirements-web.txt` includes the same lock for compatibility.
- `deploy.txt`: lists files copied to a dedicated update repository.
- `.github/workflows/mirrorchyan_*.yml`: optional MirrorChyan upload and release-note workflows.

## Current Build Workflow

`.github/workflows/build.yml` has been adapted for this project:

- Git identity is `zengliugen`.
- The `Synchronize repositories` step is kept: it syncs runtime files to the dedicated update repository `ok-script-app-pro-update` per the `deploy.txt` whitelist and creates the same tag there. It requires the `OK_GH` secret (a PAT with `contents: write` on the update repository); missing or insufficient credentials fail the step, so the build turns red.
- `pyappify-action` is pinned to `version: v1.2.3` to keep build results stable against upstream changes, and `add_version_name: true` appends the release version to installer filenames.
- `permissions: contents: write` is declared; without it the default read-only `GITHUB_TOKEN` cannot create a release and fails with 403 `Resource not accessible by integration`.
- Four artifacts are produced: one installer per profile (`China`/`Global`, named with the version such as `ok-script-app-pro-win32-China-setup-1.1.3.exe`), `ok-script-app-pro-win32-online-setup-<version>.exe`, and `ok-script-app-pro-win32.zip`. ok-script 3 removed the web frontend, so `pyappify.yml` no longer has a `Web` profile.
- The release body uses a short note for the pushed tag rather than sync-step outputs.
- Prerelease tags (`-alpha` / `-beta` / `-rc`) are automatically marked as GitHub **Pre-release** and never take the repository's Latest slot.

A build takes roughly 20 minutes, dominated by PyAppify's full Tauri/Rust compilation (~18 minutes), which GitHub Actions does not cache. To speed it up, add the `use_release` input to the `pyappify-action` step to reuse a prebuilt launcher, provided the project has not changed its icons or `pyappify.yml`.

## Release Package Filtering

This repository enables release filtering: the `Synchronize repositories` step in `build.yml` copies runtime files to the dedicated update repository per the `deploy.txt` whitelist and creates the same tag there; both profiles in `pyappify.yml` point at that update repository. Filtering is required because of a fact that is easy to miss:

### PyAppify does not read `.gitignore`

PyAppify clones the **full tree** of the target tag with libgit2 and packages it. `.gitignore` only affects `git add`, not the checkout of already-committed content. Its Rust sources contain no ignore-rule handling; the only exclusion in the entire pipeline is `__pycache__`.

As a result, **anything committed reaches the installer**, regardless of what `.gitignore` says. This repository commits `ok_templates/`, `ok_scenes/`, and `ok_tasks/` for collaboration but keeps them out of `deploy.txt`, so they never reach the installer; `assets/scenes/` is the runtime product area and ships with `assets`.

### Filtering relies on a whitelist, not on ignore rules

`deploy.txt` and `.update_repo_gitignore` are a paired mechanism, and `partial-sync-repo` is wired in here:

```
source repo --(deploy.txt whitelist)--> update repo --(git_url)--> PyAppify packaging
```

| Stage | Role |
|---|---|
| `deploy.txt` | **Positive enumeration** of entries to sync; anything unlisted never reaches the update repository |
| `partial-sync-repo` | Copies files per that list into the update repository and tags them |
| `.update_repo_gitignore` | Written into the update repository root after syncing, covering build outputs and logs |
| `pyappify.yml`'s `git_url` | Must point at the **update repository**, never the source repository |

All four are required; missing any one breaks the filtering. The whitelist is reliable precisely because it enumerates: new directories are excluded by default, so an omission causes under-inclusion, never over-inclusion.

### What downstream apps must do

The template already wires the filtering chain. Downstream apps must **switch it to their own ownership**, not reuse this project's update repository:

1. Confirm `ok_templates/`, `ok_scenes/`, and `ok_tasks/` are committed (already true here).
2. Create your own update repository (e.g. `xxx-update`) and replace the `repos` entry and secret name in `build.yml` with your own (`OK_GH` holds your PAT).
3. Point every profile's `git_url` in `pyappify.yml` at that update repository.
4. Keep `ok_templates`, `ok_scenes`, and `ok_tasks` **out of** `deploy.txt` (and confirm `tests`, `docs`, `.github`, `.codebuddy*` are absent too), while keeping every runtime directory in it. This project adds `extensions`; dropping that line ships a package that fails at startup with `ImportError`.
5. Keep `keep_releases: 30` in `pyappify.yml` so update-repository history does not grow without limit.

**Do not put the screenshots in Git LFS.** PyAppify goes through libgit2, which does not run the LFS smudge filter: CI with `actions/checkout` and `lfs: true` gets real images, but an end-user clone receives ~130-byte pointer text files that `cv2.imread` cannot decode. The failure occurs only on user machines and is silent during the build.

### Size reference

| Content | Size | Notes |
|---|---|---|
| Source repository `.git` | ~60 MB | Includes upstream history; users no longer clone it |
| Whitelisted runtime files | ~2.2 MB / 104 files | Current `deploy.txt` list (including `assets/scenes`) |
| Inlined ok-script + pyappify | ~8 MB | Produced by `inline_ok_requirements` during CI |
| One update-repository snapshot | ~10 MB | Whitelist plus inlined content |

`tests/`, `docs/`, `extensions_docs/`, the `.codebuddy` submodule, and the source history never enter the installer.

## MirrorChyan

Both MirrorChyan workflows are kept but disabled by default:

- `.github/workflows/mirrorchyan_uploading.yml`
- `.github/workflows/mirrorchyan_release_note.yml`

To enable them:

1. Create an Actions Variable `MIRRORCHYAN_ENABLED` with value `true` under **Settings → Secrets and variables → Actions**.
2. Create the secret `MirrorChyanUploadToken`.
3. Apply for a MirrorChyan resource and replace `mirrorchyan_rid` in both workflows.

PyAppify adds the release version to installer names (`add_version_name: true`), for example
`ok-script-app-pro-win32-China-setup-1.1.3.exe`. MirrorChyan matches
`ok-script-app-pro-win32-China-setup-*.exe` within the selected release.

While disabled, both jobs are skipped by their `if` condition and will not fail on missing secrets. The `Trigger MirrorChyanUploading` step in `build.yml` is gated by `vars.MIRRORCHYAN_ENABLED` as well.

## Push a Version Tag

Commit and push the initialized project, then create a tag matching `v*`:

```bash
git add .
git commit -m "Initialize project"
git push origin HEAD
git tag v0.1.0
git push origin v0.1.0
```

GitHub Actions runs the tests, packages the EXE, and creates a matching GitHub Release. Search `.github/workflows` once more for stale template repositories, names, or missing secrets before release.

After changing dependencies, recompile the locks with the project virtual environment:

```powershell
python -m piptools compile --extra qt --strip-extras --no-header --output-file requirements.txt pyproject.toml
python -m piptools compile --extra docs --strip-extras --no-header --output-file requirements-docs.txt pyproject.toml
```

The Qt lock is installed with `--no-deps`. ok-script 3 uses `ok-pyside-minimal`;
no manual removal of PySide6 dependencies is needed. Keep
`requirements-web.txt` as an include of `requirements.txt`.
