# gridlabeltool-v6

The v6 package wraps the multi-scale annotation workflow as an independent
Python project. It includes the Tkinter GUI, offline collaboration helpers,
dataset-root discovery, repair utilities, and tested multi-scale export logic.

See `USAGE_AND_MULTISCALE_EXPORT.md` for the annotation workflow, shortcut
behavior, multi-scale export modes, padding rules, and training/export guidance.

## Run

```powershell
python -m pip install -e .
python -m gridlabeltool_v6 --help
python -m gridlabeltool_v6
```

## Test

```powershell
python -m unittest discover -s tests -v
```

## Docs

- `USAGE_AND_MULTISCALE_EXPORT.md`
