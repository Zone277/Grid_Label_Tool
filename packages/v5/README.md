# gridlabeltool-v5

The v5 package wraps the layer-only annotation workflow as an independent
Python project. It keeps the Tkinter GUI in `gridlabeltool_v5.app`, ships a
versioned `config.json`, and exposes deterministic helpers for label cleanup,
numeric label selection, export folders, grid geometry, and annotation
serialization.

## Run

```powershell
python -m pip install -e .
python -m gridlabeltool_v5 --help
python -m gridlabeltool_v5
```

## Test

```powershell
python -m unittest discover -s tests -v
```

