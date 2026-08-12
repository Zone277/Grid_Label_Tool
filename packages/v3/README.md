# gridlabeltool-v3

The v3 package wraps the enhanced annotation workflow as an independent Python
project. It preserves the Tkinter GUI in `gridlabeltool_v3.app`, includes the
versioned `config.json`, and exposes testable helpers for label normalization,
grid geometry, export naming, and annotation serialization.

## Run

```powershell
python -m pip install -e .
python -m gridlabeltool_v3 --help
python -m gridlabeltool_v3
```

## Test

```powershell
python -m unittest discover -s tests -v
```

