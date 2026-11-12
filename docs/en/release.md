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

## Release Package Filtering (required for downstream apps)

This template repository enables **no** release filtering: `build.yml` has no `Synchronize repositories` step, and all three profiles in `pyappify.yml` point straight at the source repository. That leads to a fact worth understanding first.

### PyAppify does not read `.gitignore`

PyAppify clones the **full tree** of the target tag with libgit2 and packages it. `.gitignore` only affects `git add`, not the checkout of already-committed content. Its Rust sources contain no ignore-rule handling; the only exclusion in the entire pipeline is `__pycache__`.

As a result, **anything committed reaches the installer**, regardless of what `.gitignore` says. The template repository is fine on this point (`ok_templates/` is ignored), but once a downstream app commits `ok_templates/` as described in [Quick Start](getting-started.md), the source screenshots go directly into the installer.

### Filtering relies on a whitelist, not on ignore rules

`deploy.txt` and `.update_repo_gitignore` are a paired mechanism, but they only take effect once `partial-sync-repo` is wired in:

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

### Downstream checklist

Mature apps (such as ok-wuthering-waves) provide a directly reusable pattern:

1. Remove `ok_templates/` from `.gitignore` and commit it.
2. Keep `ok_templates` **out of** `deploy.txt` (and confirm `tests`, `docs`, `.github` are absent too).
3. Create a dedicated update repository (e.g. `xxx-update`) and configure the `CNB_TOKEN` / `OK_GH` secrets.
4. Restore the `Synchronize repositories` step in `build.yml` with the update repository in `repos`, `sync_list: 'deploy.txt'`, and `gitignore_file: '.update_repo_gitignore'`.
5. Point every profile's `git_url` in `pyappify.yml` at the update repository.
6. Add a bound such as `keep_releases: 30` in `pyappify.yml` so update-repository history does not grow without limit.

**Do not put the screenshots in Git LFS.** PyAppify goes through libgit2, which does not run the LFS smudge filter: CI with `actions/checkout` and `lfs: true` gets real images, but an end-user clone receives ~130-byte pointer text files that `cv2.imread` cannot decode. The failure occurs only on user machines and is silent during the build.

### Size reference

| Repository | Size | Notes |
|---|---|---|
| Source repository | ~1.2 GB | Includes `ok_templates` (~900 MB), `tests`, `docs`, git history |
| Update repository | ~145 MB | Post-whitelist, runtime content only |

A gap of more than 8x, driven mainly by source screenshots and test fixtures.

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
