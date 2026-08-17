# gridlabeltool-v7

The v7 package wraps the sampled multi-scale annotation workflow as an
independent Python project. It includes the Tkinter GUI, offline collaboration
helpers, metadata repair, dataset-root discovery, full/sample export generation,
training index balancing, the multi-scale visualizer, and tests for the
deterministic export and visualization logic.

See `USAGE_AND_MULTISCALE_EXPORT.md` for the annotation workflow, v7 export
layout, sampled/full split, training index metadata, and visualizer workflow.

## Run

```powershell
python -m pip install -e .
python -m gridlabeltool_v7 --help
python -m gridlabeltool_v7
gridlabeltool-v7-visualizer
```

## Test

```powershell
python -m unittest discover -s tests -v
```

## Docs

- `USAGE_AND_MULTISCALE_EXPORT.md`
