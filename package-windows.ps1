param([string]$OutputPath)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
if (-not $OutputPath) { $OutputPath = Join-Path (Split-Path -Parent $root) 'utopia-lite-offline-win64-py311.zip' }
$OutputPath = [System.IO.Path]::GetFullPath($OutputPath)
if (Test-Path -LiteralPath $OutputPath) { throw "Output already exists: $OutputPath. Choose another -OutputPath." }
foreach ($required in @('ollama\ollama.exe', 'wheelhouse', 'models', 'utopia-lite\requirements.txt')) {
    if (-not (Test-Path -LiteralPath (Join-Path $root $required))) { throw "Missing offline component: $required" }
}
if (-not (Get-ChildItem (Join-Path $root 'wheelhouse') -Filter '*.whl')) { throw 'The wheelhouse is empty.' }
$files = @(Get-Item (Join-Path $root 'README-WINDOWS.md'), (Join-Path $root 'prepare-ollama.py'), $PSCommandPath)
foreach ($directory in @('utopia-lite', 'ollama', 'wheelhouse', 'models')) {
    $files += Get-ChildItem (Join-Path $root $directory) -File -Recurse | Where-Object {
        $_.FullName -notmatch '\\(?:\.venv|logs|__pycache__|\.pytest_cache|\.git)\\' -and
        $_.Name -notmatch '(?:\.pyc|\.pyo|\.db-wal|\.db-shm)$'
    }
}
if (Get-ChildItem (Join-Path $root 'utopia-lite\data') -Recurse -File -Filter '*-wal' | Where-Object Length -gt 0) {
    throw 'Database WAL files contain pending data. Stop services and checkpoint the databases before packaging.'
}
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::Open($OutputPath, [System.IO.Compression.ZipArchiveMode]::Create)
try {
    $manifest = foreach ($file in ($files | Sort-Object FullName -Unique)) {
        $relative = $file.FullName.Substring($root.Length + 1).Replace('\', '/')
        $hash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLower()
        [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $file.FullName, $relative, [System.IO.Compression.CompressionLevel]::Optimal) | Out-Null
        "$hash  ./$relative"
    }
    $entry = $zip.CreateEntry('MANIFEST.sha256')
    $writer = New-Object System.IO.StreamWriter($entry.Open(), (New-Object System.Text.UTF8Encoding($false)))
    try { $writer.Write(($manifest -join "`n") + "`n") } finally { $writer.Dispose() }
} catch {
    $zip.Dispose()
    Remove-Item -LiteralPath $OutputPath
    throw
} finally {
    $zip.Dispose()
}
$checksum = (Get-FileHash -LiteralPath $OutputPath -Algorithm SHA256).Hash.ToLower()
[System.IO.File]::WriteAllText("$OutputPath.sha256", "$checksum  $([System.IO.Path]::GetFileName($OutputPath))`n", (New-Object System.Text.UTF8Encoding($false)))
Write-Host "Offline package: $OutputPath"
Write-Host "SHA256: $checksum"