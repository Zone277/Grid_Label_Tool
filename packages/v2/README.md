# gridlabeltool-v2

The v2 package wraps the edit-and-continue annotation workflow as an
independent Python project. It preserves the Tkinter GUI in
`gridlabeltool_v2.app` and exposes deterministic helpers for tests around grid
geometry and exported annotation discovery.

## Run

```powershell
python -m pip install -e .
python -m gridlabeltool_v2 --help
python -m gridlabeltool_v2
```

## Test

```powershell
python -m unittest discover -s tests -v
```

