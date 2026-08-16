# gridlabeltool-v7

The v7 package wraps the sampled multi-scale annotation workflow as an
independent Python project. It includes the Tkinter GUI, offline collaboration
helpers, metadata repair, dataset-root discovery, full/sample export generation,
training index balancing, and tests for the deterministic export logic.

## Run

```powershell
python -m pip install -e .
python -m gridlabeltool_v7 --help
python -m gridlabeltool_v7
```

## Test

```powershell
python -m unittest discover -s tests -v
```

