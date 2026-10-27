param([switch]$Setup, [switch]$Offline, [string]$Wheelhouse, [switch]$SmokeTest)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'

function Assert-FreePort([int]$Port) {
    $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $Port)
    try {
        $listener.Start()
    } catch {
        throw "Port $Port is already in use. Stop the existing service or change the port."
    } finally {
        $listener.Stop()
    }
}

function Wait-Endpoint([string]$Url, $Process) {
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        if ($Process.HasExited) {
            throw "Service exited before becoming ready: $Url. See the logs directory."
        }
        try {
            return Invoke-RestMethod -Uri $Url -TimeoutSec 2
        } catch {
            Start-Sleep -Seconds 1
        }
    }
    throw "Service startup timed out: $Url. See the logs directory."
}

try {
    if ($Setup) {
        if ($Offline) {
            if (-not $Wheelhouse) { $Wheelhouse = Join-Path (Split-Path -Parent $PSScriptRoot) 'wheelhouse' }
            if (-not (Test-Path -LiteralPath $Wheelhouse -PathType Container)) {
                throw "Offline wheelhouse not found: $Wheelhouse. Prepare it on an internet-connected Windows PC or pass -Wheelhouse."
            }
        }
        if (-not (Test-Path -LiteralPath $python)) {
            $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
            if ($launcher) {
                $launcherArguments = @('-3.11')
            } else {
                $launcher = Get-Command python.exe -ErrorAction SilentlyContinue
                if (-not $launcher) {
                    throw 'Install Python 3.11 (64-bit) from python.org, then run setup-windows.cmd again.'
                }
                $launcherArguments = @()
            }
            & $launcher.Source @launcherArguments -c "import sys,struct; assert sys.version_info[:2] == (3,11) and struct.calcsize('P') == 8, 'Python 3.11 (64-bit) required'"
            if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 (64-bit) required.' }
            & $launcher.Source @launcherArguments -m venv .venv
            if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed. Install Python 3.11 (64-bit).' }
        }
        & $python -c "import sys,struct; assert sys.version_info[:2] == (3,11) and struct.calcsize('P') == 8, 'Python 3.11 (64-bit) required'"
        if ($LASTEXITCODE -ne 0) { throw 'The existing .venv must use Python 3.11 (64-bit). Remove it and rerun setup-windows.cmd.' }
        if ($Offline) {
            & $python -m pip install --no-index --find-links $Wheelhouse -r requirements.txt
        } else {
            & $python -m pip install -r requirements.txt
        }
        if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check the wheelhouse contents or internet connection and retry.' }
        & $python -c "import sqlite3,sqlite_vec,dash,dash_cytoscape,fastapi; connection=sqlite3.connect(':memory:'); connection.enable_load_extension(True); sqlite_vec.load(connection); connection.execute('CREATE VIRTUAL TABLE smoke USING vec0(embedding float[64])'); connection.execute('CREATE VIRTUAL TABLE smoke_fts USING fts5(text)'); connection.close()"
        if ($LASTEXITCODE -ne 0) { throw 'Runtime dependency check failed.' }
        Write-Host 'Setup complete. Double-click run-windows.cmd to start.'
        exit 0
    }

    if (-not (Test-Path -LiteralPath $python)) {
        throw 'Run setup-windows.cmd first.'
    }
    if (-not $env:UTOPIA_HOST) { $env:UTOPIA_HOST = '127.0.0.1' }
    if (-not $env:UTOPIA_API_PORT) { $env:UTOPIA_API_PORT = '18000' }
    if (-not $env:UTOPIA_PORT) { $env:UTOPIA_PORT = '18050' }
    if (-not $env:UTOPIA_DATA_ROOT) { $env:UTOPIA_DATA_ROOT = Join-Path $PSScriptRoot 'data' }
    New-Item -ItemType Directory -Force -Path $env:UTOPIA_DATA_ROOT | Out-Null
    if (-not $env:UTOPIA_LITE_DB) { $env:UTOPIA_LITE_DB = Join-Path $env:UTOPIA_DATA_ROOT 'utopia-lite.db' }
    if (Test-Path -LiteralPath $env:UTOPIA_LITE_DB) {
        $snapshotDimension = & $python windows_data.py prepare $env:UTOPIA_LITE_DB
        if ($LASTEXITCODE -ne 0) { throw 'Bundled database preparation failed.' }
        if (-not $env:UTOPIA_EMBED_BACKEND -and -not $env:EMBEDDING_BASE_URL) {
            if ([int]$snapshotDimension -eq 64) {
                $env:UTOPIA_EMBED_BACKEND = 'hash'
                Write-Host 'Preserving the bundled 64-dimensional hash vector index.'
            } elseif ([int]$snapshotDimension -ne 768) {
                throw 'The bundled vector index requires an explicit compatible embedding configuration.'
            }
        }
    }
    $logs = Join-Path $PSScriptRoot 'logs'
    New-Item -ItemType Directory -Force -Path $logs | Out-Null
    Assert-FreePort ([int]$env:UTOPIA_API_PORT)
    Assert-FreePort ([int]$env:UTOPIA_PORT)

    $portableOllama = Join-Path (Split-Path -Parent $PSScriptRoot) 'ollama\ollama.exe'
    $ollama = if (Test-Path -LiteralPath $portableOllama) { Get-Item -LiteralPath $portableOllama } else { Get-Command ollama.exe -ErrorAction SilentlyContinue }
    if (-not $ollama) {
        $installedOllama = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'
        if (Test-Path -LiteralPath $installedOllama) { $ollama = Get-Item -LiteralPath $installedOllama }
    }
    $models = Join-Path (Split-Path -Parent $PSScriptRoot) 'models'
    if ($ollama -and (Test-Path -LiteralPath $models)) {
        Assert-FreePort 11435
        $env:OLLAMA_HOST = '127.0.0.1:11435'
        $env:OLLAMA_MODELS = $models
        $env:OLLAMA_NO_CLOUD = '1'
        $ollamaExe = if ($ollama.Source) { $ollama.Source } else { $ollama.FullName }
        $ollamaProcess = Start-Process -FilePath $ollamaExe -ArgumentList 'serve' -PassThru -NoNewWindow -RedirectStandardOutput (Join-Path $logs 'ollama.out.log') -RedirectStandardError (Join-Path $logs 'ollama.err.log')
        $tags = Wait-Endpoint 'http://127.0.0.1:11435/api/tags' $ollamaProcess
        $names = @($tags.models | ForEach-Object { $_.name })
        if (-not $env:LLM_BASE_URL) {
            if (-not $env:LLM_MODEL) { $env:LLM_MODEL = 'qwen2.5:1.5b' }
            if ($names -notcontains $env:LLM_MODEL) { throw "Bundled model not found: $env:LLM_MODEL" }
            $env:LLM_BASE_URL = 'http://127.0.0.1:11435/v1'
        }
        if (-not $env:EMBEDDING_BASE_URL -and $env:UTOPIA_EMBED_BACKEND -ne 'hash') {
            if (-not $env:EMBEDDING_MODEL) { $env:EMBEDDING_MODEL = 'nomic-embed-text:latest' }
            if ($names -notcontains $env:EMBEDDING_MODEL) { throw "Bundled model not found: $env:EMBEDDING_MODEL" }
            $env:EMBEDDING_BASE_URL = 'http://127.0.0.1:11435/v1'
            $env:UTOPIA_EMBED_BACKEND = 'openai'
        }
        Write-Host 'Bundled Ollama models enabled on port 11435.'
    } else {
        Write-Warning 'Ollama not found. Place the portable Windows runtime in the package-root ollama directory. Local AI is unavailable; graph and search remain usable.'
    }

    & $python seed.py
    if ($LASTEXITCODE -ne 0) { throw 'Demo data initialization failed.' }
    $apiProcess = Start-Process -FilePath $python -WorkingDirectory $PSScriptRoot -ArgumentList @('-m', 'uvicorn', 'app.api:app', '--host', $env:UTOPIA_HOST, '--port', $env:UTOPIA_API_PORT) -PassThru -NoNewWindow -RedirectStandardOutput (Join-Path $logs 'api.out.log') -RedirectStandardError (Join-Path $logs 'api.err.log')
    $null = Wait-Endpoint "http://127.0.0.1:$($env:UTOPIA_API_PORT)/openapi.json" $apiProcess
    $dashProcess = Start-Process -FilePath $python -WorkingDirectory $PSScriptRoot -ArgumentList 'dash_app.py' -PassThru -NoNewWindow -RedirectStandardOutput (Join-Path $logs 'dash.out.log') -RedirectStandardError (Join-Path $logs 'dash.err.log')
    $url = "http://127.0.0.1:$($env:UTOPIA_PORT)"
    $null = Wait-Endpoint "$url/login" $dashProcess
    if ($SmokeTest) {
        $health = Invoke-RestMethod "http://127.0.0.1:$($env:UTOPIA_API_PORT)/healthz" -TimeoutSec 10
        if ($health.status -ne 'ok') { throw 'API health check failed.' }
        $null = Invoke-RestMethod "http://127.0.0.1:$($env:UTOPIA_API_PORT)/api/graph" -TimeoutSec 10
        try {
            $null = Invoke-WebRequest "$url/_dash-layout" -UseBasicParsing -TimeoutSec 10
            throw 'Dash layout should require login.'
        } catch {
            if (-not $_.Exception.Response -or [int]$_.Exception.Response.StatusCode -ne 401) { throw }
        }
        Write-Host 'Windows launcher smoke test passed: API, graph, login page and protected Dash layout.'
        exit 0
    }
    Write-Host "Utopia Lite: $url"
    Write-Host "API docs: http://127.0.0.1:$($env:UTOPIA_API_PORT)/docs"
    Write-Host 'Keep this window open. Press Ctrl+C to stop.'
    Start-Process $url
    while (-not $apiProcess.HasExited -and -not $dashProcess.HasExited) {
        Start-Sleep -Seconds 1
    }
    throw 'An application service stopped. See the logs directory.'
} catch {
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
} finally {
    foreach ($process in @($dashProcess, $apiProcess, $ollamaProcess)) {
        if ($process -and -not $process.HasExited) {
            & taskkill.exe /PID $process.Id /T /F | Out-Null
        }
    }
}