# Release Assets

Executable files are published as GitHub Release attachments. They are not
tracked in Git because they are generated binaries and some historical builds
are too large for normal source control.

## v1 Asset

Upload the existing v1 Windows build to Release `v1.0.0` with this name:

```text
GridLabelTool-v1.0.0-windows-grid-label-tool.exe
```

The local source asset is kept outside this repository in the local release
asset staging folder:

```text
..\github_release_assets\GridLabelTool-v1.0.0-windows-grid-label-tool.exe
```
