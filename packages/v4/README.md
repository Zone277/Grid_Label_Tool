# gridlabeltool-v4

The v4 package wraps the dynamic-label annotation workflow as an independent
Python project. It preserves the Tkinter GUI in `gridlabeltool_v4.app`, ships
the versioned `config.json`, and exposes deterministic helpers for dynamic
labels, shortcut normalization, export folders, grid geometry, and annotation
serialization.

## Run

```powershell
python -m pip install -e .
python -m gridlabeltool_v4 --help
python -m gridlabeltool_v4
```

## Test

```powershell
python -m unittest discover -s tests -v
```

