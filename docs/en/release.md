# Packaging and Release

## Release Files

- `.github/workflows/build.yml`: watches `v*` tags, tests, syncs updates, packages, and creates a GitHub Release.
- `pyappify.yml`: defines the app name, entry point, icon, Python version, and update repositories.
- `pyproject.toml`: defines the Qt, web, and documentation dependency profiles.
- `requirements.txt` and `requirements-web.txt`: compiled installation locks for the TOML profiles.
- `deploy.txt`: lists files copied to a dedicated update repository.
- `.github/workflows/mirrorchyan_*.yml`: optional MirrorChyan upload and release-note workflows.

## Current Build Workflow

`.github/workflows/build.yml` has been adapted for this project:

- Git identity is `zengliugen`.
- The CNB and dedicated update-repository `Sync Repositories` step was removed, so builds no longer need template secrets such as `CNB_TOKEN` or `OK_GH`.
- `pyappify-action` is pinned to `version: v1.2.3` to keep build results stable against upstream changes.
- Artifacts are `ok-script-app-pro-win32-online-setup.exe` (online installer) and `ok-script-app-pro-win32.zip` (offline bundle), with Release links pointing at this repository.
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
python -m piptools compile --extra web --strip-extras --no-header --output-file requirements-web.txt pyproject.toml
python -m piptools compile --extra docs --strip-extras --no-header --output-file requirements-docs.txt pyproject.toml
```

The Qt lock is installed with `--no-deps`. After compilation, remove the
generated `pyside6` and `pyside6-addons` entries while retaining
`pyside6-essentials`, so Fluent Widgets does not restore unused PySide6 modules.
