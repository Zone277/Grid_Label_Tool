# GridLabelTool

Open-source packaging workspace for the grid-based annotation tool series.

This repository is being published as staged updates. The packaged slices add
independent Python projects under `packages/v1`, `packages/v2`, and later
version folders, each with a preserved GUI app entry and testable core helpers.

## Packaged Slices

| Version | Package | Focus |
|---|---|---|
| v1 | `packages/v1` | first-pass grid annotation |
| v2 | `packages/v2` | edit and continue existing annotations |
| v3 | `packages/v3` | enhanced layer and direction annotation |
| v4 | `packages/v4` | dynamic layer-label configuration |
| v5 | `packages/v5` | layer-only annotation with numeric selection |

## v1 Quick Check

```powershell
cd packages\v1
python -m pip install -e .
python -m gridlabeltool_v1 --help
python -m unittest discover -s tests -v
```

## v2 Quick Check

```powershell
cd packages\v2
python -m pip install -e .
python -m gridlabeltool_v2 --help
python -m unittest discover -s tests -v
```

## v3 Quick Check

```powershell
cd packages\v3
python -m pip install -e .
python -m gridlabeltool_v3 --help
python -m unittest discover -s tests -v
```

## v4 Quick Check

```powershell
cd packages\v4
python -m pip install -e .
python -m gridlabeltool_v4 --help
python -m unittest discover -s tests -v
```

## v5 Quick Check

```powershell
cd packages\v5
python -m pip install -e .
python -m gridlabeltool_v5 --help
python -m unittest discover -s tests -v
```

To launch a GUI from source:

```powershell
cd packages\v2
python -m gridlabeltool_v2
```

## Current Structure

```text
packages/
  v1/
    pyproject.toml
    src/gridlabeltool_v1/
    tests/
  v2/
    pyproject.toml
    src/gridlabeltool_v2/
    tests/
  v3/
    pyproject.toml
    src/gridlabeltool_v3/
    tests/
  v4/
    pyproject.toml
    src/gridlabeltool_v4/
    tests/
  v5/
    pyproject.toml
    src/gridlabeltool_v5/
    tests/
```

Windows executables are distributed through GitHub Releases instead of being
committed to the repository.
