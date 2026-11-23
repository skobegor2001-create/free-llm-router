# Free LLM Router - creates Start menu and Desktop shortcuts.
# Called by install.cmd; can be re-run manually:
#   powershell -ExecutionPolicy Bypass -File windows\setup_shortcuts.ps1
# Удаление ярлыков:
#   powershell -ExecutionPolicy Bypass -File windows\setup_shortcuts.ps1 -Uninstall
param(
    [string]$Root = "",
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"

if (-not $Root) {
    $Root = Split-Path -Parent $PSScriptRoot      # windows\ -> project root
}

if ($Uninstall) {
    $startDir = Join-Path ([Environment]::GetFolderPath("Programs")) "Free LLM Router"
    if (Test-Path -LiteralPath $startDir) {
        Remove-Item -LiteralPath $startDir -Recurse -Force -ErrorAction SilentlyContinue
    }
    $desktop = [Environment]::GetFolderPath("Desktop")
    if ($desktop) {
        $lnk = Join-Path $desktop "Free LLM Router.lnk"
        if (Test-Path -LiteralPath $lnk) {
            Remove-Item -LiteralPath $lnk -Force -ErrorAction SilentlyContinue
        }
    }
    Write-Host "  Shortcuts removed."
    exit 0
}

$launcher = Join-Path $Root "launcher.cmd"
$icon = Join-Path $Root "windows\assets\app.ico"
$description = "Free neural networks: chat and OpenAI-compatible gateway for your agent"

if (-not (Test-Path -LiteralPath $launcher)) {
    Write-Host "  ERROR: launcher.cmd not found in $Root"
    exit 1
}

$shell = New-Object -ComObject WScript.Shell

function New-Shortcut {
    param(
        [string]$Path,
        [string]$Target,
        [string]$WorkDir,
        [string]$Icon,
        [string]$Description
    )
    $dir = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    $shortcut = $shell.CreateShortcut($Path)
    $shortcut.TargetPath = $Target
    $shortcut.WorkingDirectory = $WorkDir
    $shortcut.WindowStyle = 1
    $shortcut.Description = $Description
    if (Test-Path -LiteralPath $Icon) {
        $shortcut.IconLocation = "$Icon,0"
    }
    $shortcut.Save()
}

$startDir = Join-Path ([Environment]::GetFolderPath("Programs")) "Free LLM Router"
New-Shortcut -Path (Join-Path $startDir "Free LLM Router.lnk") `
    -Target $launcher -WorkDir $Root -Icon $icon -Description $description

$desktop = [Environment]::GetFolderPath("Desktop")
if ($desktop) {
    New-Shortcut -Path (Join-Path $desktop "Free LLM Router.lnk") `
        -Target $launcher -WorkDir $Root -Icon $icon -Description $description
}

Write-Host "  Shortcuts: Start menu > Free LLM Router, and Desktop"
exit 0
