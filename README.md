# GridLabelTool

Open-source packaging workspace for the grid-based annotation tool series.

This repository is being published as staged updates. The first packaged
slice adds `gridlabeltool-v1` under `packages/v1` as an independent Python
package with a preserved GUI app entry and testable core grid helpers.

## v1 Package Slice

```powershell
cd packages\v1
python -m pip install -e .
python -m gridlabeltool_v1 --help
python -m unittest discover -s tests -v
```

To launch the v1 GUI from source:

```powershell
cd packages\v1
python -m gridlabeltool_v1
```

## Current Structure

```text
packages/
  v1/
    pyproject.toml
    src/gridlabeltool_v1/
    tests/
```

Windows executables are distributed through GitHub Releases instead of being
committed to the repository.
