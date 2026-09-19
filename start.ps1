<#
.SYNOPSIS
    Start ULUGBEK AI locally: PostgreSQL, the API, and the Web Control Center.

.DESCRIPTION
    One command instead of three terminals. The script is idempotent: anything
    already running is left alone, so re-running it after a crash only restarts
    the part that died.

    It starts PostgreSQL in Docker, applies migrations, launches the API and the
    frontend in their own windows, waits until both actually answer, and opens
    the browser.

    No secret is ever read, printed, or written by this script. It reports only
    whether a key is configured, never its value.

.PARAMETER FrontendPort
    Port for the Web Control Center. Default 3001; if it is taken the script
    moves to the next free port automatically.

.PARAMETER BackendPort
    Port the API listens on. Default 8000.

.PARAMETER SkipMigrations
    Do not run "alembic upgrade head". Use when the schema is known current.

.PARAMETER NoBrowser
    Do not open the browser at the end.

.EXAMPLE
    .\start.ps1

.EXAMPLE
    .\start.ps1 -FrontendPort 3005 -NoBrowser
#>

[CmdletBinding()]
param(
    [int]$FrontendPort = 3001,
    [int]$BackendPort = 8000,
    [switch]$SkipMigrations,
    [switch]$NoBrowser
)

# Native commands (docker, alembic) write progress to stderr; under
# 'Stop' Windows PowerShell would treat that as a fatal error. Exit codes
# are checked explicitly instead.
$ErrorActionPreference = 'Continue'
$root = $PSScriptRoot
$frontendDir = Join-Path $root 'frontend'
$venvPython = Join-Path $root '.venv\Scripts\python.exe'

# ---------------------------------------------------------------- helpers ---

function Write-Step([string]$Text) {
    Write-Host ''
    Write-Host "==> $Text" -ForegroundColor Cyan
}

function Write-Ok([string]$Text) {
    Write-Host "    OK  $Text" -ForegroundColor Green
}

function Write-Warn([string]$Text) {
    Write-Host "    !   $Text" -ForegroundColor Yellow
}

function Fail([string]$Text, [string[]]$Hints) {
    Write-Host ''
    Write-Host "XATO: $Text" -ForegroundColor Red
    foreach ($hint in $Hints) {
        Write-Host "      $hint" -ForegroundColor Gray
    }
    Write-Host ''
    exit 1
}

function Test-PortListening([int]$Port) {
    $found = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    return $null -ne $found
}

function Get-FreePort([int]$Preferred) {
    for ($port = $Preferred; $port -lt ($Preferred + 20); $port++) {
        if (-not (Test-PortListening $port)) { return $port }
    }
    Fail "Bo'sh port topilmadi ($Preferred dan boshlab 20 ta band)." @(
        '-FrontendPort bilan boshqa portni ko''rsating.'
    )
}

function Test-HttpOk([string]$Url) {
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 500
    } catch {
        return $false
    }
}

function Wait-ForHttp([string]$Url, [int]$TimeoutSeconds) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-HttpOk $Url) { return $true }
        Start-Sleep -Seconds 1
        Write-Host '.' -NoNewline
    }
    Write-Host ''
    return $false
}

# Launch a long-running process in its own window, titled so it is findable
# in the taskbar. The window stays open after the process exits so a crash
# message remains readable.
function Start-InNewWindow([string]$Title, [string]$WorkDir, [string]$Command) {
    $inner = "`$host.UI.RawUI.WindowTitle = '$Title'; Set-Location -LiteralPath '$WorkDir'; $Command"
    Start-Process -FilePath 'powershell.exe' `
        -ArgumentList '-NoExit', '-NoProfile', '-Command', $inner | Out-Null
}

# ------------------------------------------------------------------ start ---

Write-Host ''
Write-Host '  ULUGBEK AI - local start' -ForegroundColor White
Write-Host "  $root" -ForegroundColor DarkGray

# --- 1. Configuration file -------------------------------------------------

Write-Step 'Konfiguratsiya'

$envFile = Join-Path $root '.env'
if (-not (Test-Path -LiteralPath $envFile)) {
    Fail '.env fayli topilmadi.' @(
        'Namunadan nusxa oling va ANTHROPIC_API_KEY ni to''ldiring:',
        '  Copy-Item .env.example .env',
        '(.env hech qachon gitga tushmaydi.)'
    )
}
Write-Ok '.env mavjud'

# --- 2. Docker + PostgreSQL ------------------------------------------------

Write-Step 'PostgreSQL (Docker)'

$dockerCmd = Get-Command docker -ErrorAction SilentlyContinue
if ($null -eq $dockerCmd) {
    Fail 'docker buyrug''i topilmadi.' @(
        'Docker Desktop o''rnatilganini va PATH da ekanini tekshiring.'
    )
}

docker info 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    # Docker Desktop is not up. Launching it is the one thing the user would
    # otherwise have to do by hand before every session.
    $desktopExe = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
    $desktopRunning = Get-Process -Name 'Docker Desktop' -ErrorAction SilentlyContinue
    if ((Test-Path -LiteralPath $desktopExe) -and ($null -eq $desktopRunning)) {
        Write-Warn 'Docker Desktop ochilmoqda...'
        Start-Process -FilePath $desktopExe | Out-Null
    } else {
        Write-Warn 'Docker javob bermayapti - Docker Desktop kutilmoqda...'
    }
    $dockerDeadline = (Get-Date).AddSeconds(180)
    while ((Get-Date) -lt $dockerDeadline) {
        Start-Sleep -Seconds 3
        Write-Host '.' -NoNewline
        docker info 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) { break }
    }
    Write-Host ''
    if ($LASTEXITCODE -ne 0) {
        Fail 'Docker Desktop ishga tushmadi.' @(
            'Docker Desktop dasturini qo''lda oching, "Engine running" bo''lishini',
            'kuting, keyin shu skriptni qayta ishga tushiring.'
        )
    }
}
Write-Ok 'Docker ishlayapti'

Push-Location $root
try {
    docker compose up -d postgres 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Fail 'docker compose postgres ni ko''tara olmadi.' @(
            'Qo''lda ishga tushirib xatoni ko''ring:',
            '  docker compose up postgres'
        )
    }
    $containerId = (docker compose ps -q postgres | Select-Object -First 1)
} finally {
    Pop-Location
}

if ([string]::IsNullOrWhiteSpace($containerId)) {
    Fail 'postgres konteyneri topilmadi.' @('  docker compose ps')
}

Write-Host '    ' -NoNewline
$pgDeadline = (Get-Date).AddSeconds(60)
$pgReady = $false
while ((Get-Date) -lt $pgDeadline) {
    $status = (docker inspect -f '{{.State.Health.Status}}' $containerId 2>$null)
    if ($status -eq 'healthy') { $pgReady = $true; break }
    Start-Sleep -Seconds 2
    Write-Host '.' -NoNewline
}
Write-Host ''

if (-not $pgReady) {
    Fail 'PostgreSQL 60 soniyada tayyor bo''lmadi.' @(
        'Loglarni ko''ring:',
        '  docker compose logs postgres'
    )
}
Write-Ok 'PostgreSQL tayyor'

# --- 3. Python environment + migrations ------------------------------------

Write-Step 'Backend muhiti'

if (-not (Test-Path -LiteralPath $venvPython)) {
    Fail '.venv topilmadi.' @(
        'Virtual muhitni yarating:',
        '  python -m venv .venv',
        '  .\.venv\Scripts\python.exe -m pip install -e ".[dev]"'
    )
}
Write-Ok '.venv mavjud'

if ($SkipMigrations) {
    Write-Warn 'Migratsiyalar o''tkazib yuborildi (-SkipMigrations)'
} else {
    & $venvPython -m alembic upgrade head 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Fail 'alembic upgrade head muvaffaqiyatsiz.' @(
            'Xatoni to''liq ko''rish uchun:',
            '  .\.venv\Scripts\python.exe -m alembic upgrade head',
            'Eslatma: Windows da DATABASE_URL ichida localhost emas, 127.0.0.1 bo''lsin.'
        )
    }
    Write-Ok 'Migratsiyalar qo''llandi'
}

# --- 4. API ----------------------------------------------------------------

Write-Step "API (port $BackendPort)"

$healthUrl = "http://127.0.0.1:$BackendPort/api/health"

if (Test-HttpOk $healthUrl) {
    Write-Ok 'Allaqachon ishlayapti - qayta ishga tushirilmadi'
} else {
    if (Test-PortListening $BackendPort) {
        Fail "$BackendPort porti band, lekin javob bergan dastur bizning API emas." @(
            'Kim band qilganini ko''ring:',
            "  netstat -ano | findstr `":$BackendPort`" | findstr LISTENING",
            'Yoki boshqa portni tanlang: .\start.ps1 -BackendPort 8010'
        )
    }
    Start-InNewWindow -Title 'ULUGBEK AI - API' -WorkDir $root `
        -Command '.\.venv\Scripts\python.exe -m ulugbek_ai'
    Write-Host '    ' -NoNewline
    if (-not (Wait-ForHttp $healthUrl 60)) {
        Fail 'API 60 soniyada javob bermadi.' @(
            '"ULUGBEK AI - API" oynasidagi xato xabarini o''qing.'
        )
    }
    Write-Host ''
    Write-Ok 'API javob berdi'
}

# Report dependency state without touching any secret value.
try {
    $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 5 -ErrorAction Stop
    $dbMark = if ($health.database.connected) { 'ulangan' } else { 'ULANMAGAN' }
    $llmMark = if ($health.llm.configured) { 'sozlangan' } else { 'SOZLANMAGAN' }
    Write-Host "    Database: $dbMark   Claude: $llmMark ($($health.llm.model))   Tools: $($health.tools.count)" -ForegroundColor DarkGray
    if (-not $health.llm.configured) {
        Write-Warn '.env ichida ANTHROPIC_API_KEY yo''q - agent ishlamaydi.'
    }
} catch {
    Write-Warn 'Health javobini o''qib bo''lmadi (API baribir ishlayapti).'
}

# --- 5. Web Control Center -------------------------------------------------

Write-Step 'Web Control Center'

if (-not (Test-Path -LiteralPath (Join-Path $frontendDir 'node_modules'))) {
    Fail 'frontend/node_modules topilmadi.' @(
        'Paketlarni o''rnating:',
        '  cd frontend',
        '  npm install'
    )
}

$envLocal = Join-Path $frontendDir '.env.local'
$apiBase = "NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:$BackendPort/api"
if (-not (Test-Path -LiteralPath $envLocal)) {
    Set-Content -LiteralPath $envLocal -Value $apiBase -Encoding Ascii
    Write-Ok '.env.local yaratildi'
} else {
    Write-Ok '.env.local mavjud'
}

$port = Get-FreePort $FrontendPort
if ($port -ne $FrontendPort) {
    Write-Warn "$FrontendPort band - $port ishlatiladi"
}

$frontendUrl = "http://localhost:$port"
Start-InNewWindow -Title "ULUGBEK AI - Web ($port)" -WorkDir $frontendDir `
    -Command "npx next dev -p $port"

Write-Host '    ' -NoNewline
if (-not (Wait-ForHttp $frontendUrl 120)) {
    Fail 'Frontend 120 soniyada javob bermadi.' @(
        "`"ULUGBEK AI - Web ($port)`" oynasidagi xabarni o'qing.",
        'Birinchi kompilyatsiya sekin bo''lishi mumkin - biroz kuting va',
        "brauzerda $frontendUrl ni oching."
    )
}
Write-Host ''
Write-Ok 'Frontend tayyor'

# --- 6. Done ---------------------------------------------------------------

Write-Host ''
Write-Host '  Tayyor.' -ForegroundColor Green
Write-Host "  Web:  $frontendUrl" -ForegroundColor White
Write-Host "  API:  http://127.0.0.1:$BackendPort/api/health" -ForegroundColor DarkGray
Write-Host "  Docs: http://127.0.0.1:$BackendPort/docs" -ForegroundColor DarkGray
Write-Host ''
Write-Host '  Ikkita yangi oyna ochildi - ularni yopmang.' -ForegroundColor DarkGray
Write-Host '  Eslatma: oyna ichiga sichqoncha bilan bossangiz konsol "tanlash"' -ForegroundColor DarkGray
Write-Host '  rejimiga o''tib chiqishni to''xtatadi. Esc bosing.' -ForegroundColor DarkGray
Write-Host ''

if (-not $NoBrowser) {
    Start-Process $frontendUrl | Out-Null
}
