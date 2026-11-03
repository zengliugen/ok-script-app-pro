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
- The CNB and dedicated update-repository `Sync Repositories` step was removed, so builds no longer need template secrets such as `CNB_TOKEN` or `OK_GH`.
- `pyappify-action` is pinned to `version: v1.2.3` to keep build results stable against upstream changes, and `add_version_name: true` appends the release version to installer filenames.
- `permissions: contents: write` is declared; without it the default read-only `GITHUB_TOKEN` cannot create a release and fails with 403 `Resource not accessible by integration`.
- Four artifacts are produced: one installer per profile (`China`/`Global`, named with the version such as `ok-script-app-pro-win32-China-setup-1.1.3.exe`), `ok-script-app-pro-win32-online-setup-<version>.exe`, and `ok-script-app-pro-win32.zip`. ok-script 3 removed the web frontend, so `pyappify.yml` no longer has a `Web` profile.
- The release body uses a short note for the pushed tag instead of sync-step outputs.

A build takes roughly 20 minutes, dominated by PyAppify's full Tauri/Rust compilation (~18 minutes), which GitHub Actions does not cache. To speed it up, add the `use_release` input to the `pyappify-action` step to reuse a prebuilt launcher, provided the project has not changed its icons or `pyappify.yml`.

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
