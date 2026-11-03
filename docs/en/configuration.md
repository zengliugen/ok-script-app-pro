# App and Runtime Target Configuration

## App Settings

Review at least these settings in `src/config.py`:

- `gui_title`: application window title.
- `gui_icon`: GUI icon path.
- `supported_resolution`: supported aspect ratio and minimum resolution.
- `links`: project, support, and community links.
- `onetime_tasks`, `trigger_tasks`: task registration lists.

## Runtime Targets

Configure at least one of `windows`, `adb`, or `browser`. A project may support multiple target types.

### Native Windows Game

Configure these `windows` values:

- `exe`: game executable filenames.
- `hwnd_class`: optional window class for more accurate matching.
- `interaction`: allowed input methods in priority order.
- `capture_method`: allowed capture methods in priority order.
- HDR and background-capture options.

### Android Emulator or Device

Add package names to `adb.packages`. MuMu can use native capture and input; other emulators and devices generally use ADB.

### Browser Game

Uncomment the `browser` example and set:

```python
'browser': {
    'url': 'https://example.com/game',
    'nick': 'Browser',
    'resolution': (1280, 720),
},
```

Browser targets also require `playwright`. Add it to each applicable profile in
`pyproject.toml`, then recompile the corresponding `requirements.txt` or
`requirements-web.txt` so local and GitHub build environments install it.

## Replace Icons

Replace `icons/icon.png` and `icons/icon.ico`. Keeping the filenames avoids extra configuration. If they change, update their paths in `src/config.py` and `pyappify.yml`.

## Update Repositories

All three profiles in `pyappify.yml` point at the source repository `https://github.com/zengliugen/ok-script-app-pro.git`, so no separate update repository is needed during early development.

- Application name: `ok-script-app-pro`.
- `git_url`: the source repository, which the installer's auto-update pulls from.
- To reduce update download size later, create a lightweight update repository and adjust the sync step in `.github/workflows/build.yml`.

## Checks After Syncing Upstream

After merging updates from `upstream` (`ok-oldking/ok-script-app`), re-verify that the following were not overwritten:

- `gui_title` and `links` in `src/config.py`.
- `name` and each profile's `git_url` in `pyappify.yml`.
- Git identity, installer names, and download links in `.github/workflows/build.yml`.
