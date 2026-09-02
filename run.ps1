# Start O'dell Tech Shopping.
#
#   .\run.ps1              -> uses whatever DB_ENGINE says in .env
#   .\run.ps1 -Mysql       -> force MySQL (starts the server if it is not running)
#   .\run.ps1 -Port 8001   -> different port
#
param(
    [switch]$Mysql,
    [int]$Port = 8000
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$python = Join-Path $PSScriptRoot 'venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    Write-Host "Virtual environment missing. Run:" -ForegroundColor Yellow
    Write-Host "  python -m venv venv"
    Write-Host "  venv\Scripts\activate"
    Write-Host "  pip install -r requirements.txt"
    exit 1
}

# The Bengali Taka sign needs UTF-8 on Windows consoles.
$env:PYTHONUTF8 = '1'

# Start MySQL when it is the configured backend, whether that came from the
# -Mysql switch or from DB_ENGINE=mysql in .env. Forgetting to start it is the
# single most common reason the app fails to boot.
$wantsMysql = $Mysql
if (-not $wantsMysql -and (Test-Path (Join-Path $PSScriptRoot '.env'))) {
    $wantsMysql = (Get-Content (Join-Path $PSScriptRoot '.env') |
        Where-Object { $_ -match '^\s*DB_ENGINE\s*=\s*(mysql|mariadb)\s*$' }).Count -gt 0
}

if ($wantsMysql) {
    if ($Mysql) { $env:DB_ENGINE = 'mysql' }

    $running = Test-NetConnection -ComputerName 127.0.0.1 -Port 3307 `
        -InformationLevel Quiet -WarningAction SilentlyContinue
    if (-not $running) {
        Write-Host "Starting MySQL 8.4 on port 3307..." -ForegroundColor Cyan
        Start-Process -FilePath 'C:\Users\Asus\mysql84\bin\mysqld.exe' `
            -ArgumentList '--defaults-file=C:\Users\Asus\mysql84\my.ini' `
            -WindowStyle Hidden
        for ($i = 0; $i -lt 30; $i++) {
            Start-Sleep -Milliseconds 500
            if (Test-NetConnection -ComputerName 127.0.0.1 -Port 3307 `
                    -InformationLevel Quiet -WarningAction SilentlyContinue) { break }
        }
    }
    Write-Host "Database: MySQL - pos_system on 127.0.0.1:3307" -ForegroundColor Green
} else {
    Write-Host "Database: SQLite (db.sqlite3)" -ForegroundColor Green
}

& $python manage.py migrate --noinput
Write-Host ""
Write-Host "Open http://127.0.0.1:$Port/   (admin / Pos@12345)" -ForegroundColor Green
Write-Host ""
& $python manage.py runserver "127.0.0.1:$Port"
