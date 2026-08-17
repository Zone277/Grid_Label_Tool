# Release Assets

Executable files are published as GitHub Release attachments. They are not
tracked in Git because they are generated binaries and some historical builds
are too large for normal source control.

## Checksums

Expected file sizes and SHA256 hashes are tracked in:

```text
docs/release-assets.sha256.json
```

To verify a local release asset on Windows:

```powershell
Get-FileHash ..\github_release_assets\GridLabelTool-v7.0.0-windows-enhanced-grid-label-tool.exe -Algorithm SHA256
```

Compare the reported hash with the matching `SHA256` value in the checksum
manifest before uploading or replacing a Release attachment.

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

## v2 Asset

Upload the existing v2 Windows build to Release `v2.0.0` with this name:

```text
GridLabelTool-v2.0.0-windows-edit-label-tool.exe
```

The local source asset is kept outside this repository in the local release
asset staging folder:

```text
..\github_release_assets\GridLabelTool-v2.0.0-windows-edit-label-tool.exe
```

## v3 Asset

Upload the existing v3 Windows build to Release `v3.0.0` with this name:

```text
GridLabelTool-v3.0.0-windows-enhanced-grid-label-tool.exe
```

The local source asset is kept outside this repository in the local release
asset staging folder:

```text
..\github_release_assets\GridLabelTool-v3.0.0-windows-enhanced-grid-label-tool.exe
```

## v4 Asset

Upload the existing v4 Windows build to Release `v4.0.0` with this name:

```text
GridLabelTool-v4.0.0-windows-enhanced-grid-label-tool.exe
```

The local source asset is kept outside this repository in the local release
asset staging folder:

```text
..\github_release_assets\GridLabelTool-v4.0.0-windows-enhanced-grid-label-tool.exe
```

## v5 Asset

Upload the existing v5 Windows build to Release `v5.0.0` with this name:

```text
GridLabelTool-v5.0.0-windows-enhanced-grid-label-tool.exe
```

The local source asset is kept outside this repository in the local release
asset staging folder:

```text
..\github_release_assets\GridLabelTool-v5.0.0-windows-enhanced-grid-label-tool.exe
```

## v6 Asset

Upload the existing v6 Windows build to Release `v6.0.0` with this name:

```text
GridLabelTool-v6.0.0-windows-enhanced-grid-label-tool.exe
```

The local source asset is kept outside this repository in the local release
asset staging folder:

```text
..\github_release_assets\GridLabelTool-v6.0.0-windows-enhanced-grid-label-tool.exe
```

## v7 Main Asset

Upload the existing v7 Windows annotation build to Release `v7.0.0` with this
name:

```text
GridLabelTool-v7.0.0-windows-enhanced-grid-label-tool.exe
```

The local source asset is kept outside this repository in the local release
asset staging folder:

```text
..\github_release_assets\GridLabelTool-v7.0.0-windows-enhanced-grid-label-tool.exe
```

## v7 Visualizer Asset

Upload the existing v7 Windows visualizer build to Release `v7.0.0` with this
name:

```text
GridLabelTool-v7.0.0-windows-multiscale-visualizer.exe
```

The local source asset is kept outside this repository in the local release
asset staging folder:

```text
..\github_release_assets\GridLabelTool-v7.0.0-windows-multiscale-visualizer.exe
```
