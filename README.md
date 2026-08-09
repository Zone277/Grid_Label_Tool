# GridLabelTool

Open-source packaging workspace for the grid-based annotation tool series.

This repository is being published as staged daily updates. The first packaged
slice adds `gridlabeltool-v1` under `packages/v1` as an independent Python
package with a preserved GUI app entry and testable core grid helpers.

## Day 2 Partial Package

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
