# Windows Build Notes

This repository tracks source packages under `packages/`. Windows executables
are release artifacts and are not committed to Git.

## Environment

Use Windows with Python 3.10 or newer.

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
```

Install the package version you want to build:

```powershell
python -m pip install -e packages\v7
```

## GUI Smoke Check

Before building an executable, verify the package entry point:

```powershell
python -m gridlabeltool_v7 --help
python -m unittest discover -s packages\v7\tests -v
```

For v7, the visualizer entry point is installed as:

```powershell
gridlabeltool-v7-visualizer
```

## Build Policy

Do not commit generated files from:

```text
build/
dist/
.packenv/
__pycache__/
*.egg-info/
*.exe
*.zip
```

Move finished executables to the local release asset staging folder outside the
repository:

```text
..\github_release_assets
```

Then verify the file hash against `docs/release-assets.sha256.json` before
uploading the asset to a GitHub Release.

