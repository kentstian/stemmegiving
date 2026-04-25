# Run this script from the voting_app folder in PowerShell.
# It creates or activates a virtual environment and starts the Flask application.

$venvPath = Join-Path $PSScriptRoot '.venv'

if (-not (Test-Path $venvPath)) {
    python -m venv $venvPath
}

$activateScript = Join-Path $venvPath 'Scripts\Activate.ps1'

if (-not (Test-Path $activateScript)) {
    Write-Error 'Virtual environment activation script not found.'
    exit 1
}

Write-Host 'Activating virtual environment...'
. $activateScript

Write-Host 'Installing requirements if needed...'
pip install -r (Join-Path $PSScriptRoot 'requirements.txt')

Write-Host 'Starting Flask app...'
python (Join-Path $PSScriptRoot 'app.py')
