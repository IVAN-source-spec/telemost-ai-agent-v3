param(
    [Parameter(Mandatory = $true)]
    [string]$AudioPath,

    [Parameter(Mandatory = $true)]
    [string]$ApiKey,

    [int]$Speakers,
    [int]$MinSpeakers,
    [int]$MaxSpeakers,
    [string]$MediaUrl,
    [int]$PollSeconds = 15,
    [int]$TimeoutSeconds = 3600
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonExe = "C:\Users\OMEN\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$runnerPath = Join-Path $scriptDir "run_pyannote_job.py"

if (-not (Test-Path $pythonExe)) {
    throw "Python runtime not found: $pythonExe"
}

if (-not (Test-Path $runnerPath)) {
    throw "run_pyannote_job.py not found: $runnerPath"
}

if (-not (Test-Path $AudioPath)) {
    throw "Audio file not found: $AudioPath"
}

$env:PYANNOTE_API_KEY = $ApiKey

$command = @(
    $pythonExe,
    $runnerPath,
    $AudioPath,
    "--poll-seconds", $PollSeconds,
    "--timeout-seconds", $TimeoutSeconds
)

if ($PSBoundParameters.ContainsKey("Speakers")) {
    $command += @("--speakers", $Speakers)
} else {
    if ($PSBoundParameters.ContainsKey("MinSpeakers")) {
        $command += @("--min-speakers", $MinSpeakers)
    }
    if ($PSBoundParameters.ContainsKey("MaxSpeakers")) {
        $command += @("--max-speakers", $MaxSpeakers)
    }
}

if ($MediaUrl) {
    $command += @("--media-url", $MediaUrl)
}

Write-Host "Running pyannote job..." -ForegroundColor Cyan
Write-Host ($command -join " ") -ForegroundColor DarkGray
& $command[0] $command[1..($command.Length - 1)]
