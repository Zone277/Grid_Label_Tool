# gridlabeltool-v1

The v1 package is the first packaged slice of GridLabelTool. It preserves the
original Tkinter GUI behavior in `gridlabeltool_v1.app` and adds a small
`core` module for deterministic tests around grid geometry and export naming.

## Run

```powershell
python -m pip install -e .
python -m gridlabeltool_v1 --help
python -m gridlabeltool_v1
```

## Test

```powershell
python -m unittest discover -s tests -v
```

