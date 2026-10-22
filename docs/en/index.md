# ok-script-app-pro

[中文](../index.md)

ok-script-app-pro is a capability-extension project built from the [ok-script-app](https://github.com/ok-oldking/ok-script-app) template and powered by [ok-script](https://github.com/ok-oldking/ok-script). It covers native Windows games, Android emulators, and browser games.

It is not a finished automation tool for a specific game. It keeps the upstream runnable GUI, task and configuration-widget examples, OCR, template matching, tests, i18n, EXE packaging, and update/release configuration, and layers additional extensions on top.

## Start Here

1. Prepare the local environment with the [Quick start](getting-started.md).
2. Confirm runtime targets and project identity in [App configuration](configuration.md).
3. Add extension tasks with [Task development](tasks.md).
4. Build an EXE with [Packaging and release](release.md).

## Relationship to Upstream

This project tracks [ok-script-app](https://github.com/ok-oldking/ok-script-app) through the `upstream` remote: upstream provides the template skeleton and shared capabilities, while extensions are developed here and the installers and update source point at this repository.

## Demo

### API List and Script Recording

![API list and script recording](../images/image_scripting.png)

### Capture and Interaction Methods

![Capture and interaction methods](../images/image_capture.png)

### Annotation and Template Matching

![Template matching](../images/image_template.png)

![Annotation management](../images/image_markup.png)

## Included

- Upstream `MyOneTimeTask` and `MyTriggerTask` examples preserved.
- Drop-down, boolean, numeric, text, list, multi-selection, file, global configuration, and button widgets.
- OCR, relative-region recognition, and template matching examples.
- `ConfigOption` global configuration and `TaskTestCase` test examples.
- gettext i18n sources and compiled catalogs.
- PyAppify and GitHub Actions packaging/release configuration.

## Further Reading (Chinese)

- [Intro to game automation](https://github.com/ok-oldking/ok-script/blob/master/docs/intro_to_automation/README.md)
- [ok-script quick start](https://github.com/ok-oldking/ok-script/blob/master/docs/quick_start/README.md)
- [After quick start](https://github.com/ok-oldking/ok-script/blob/master/docs/after_quick_start/README.md)
- [API documentation](https://github.com/ok-oldking/ok-script/blob/master/docs/api_doc/README.md)
