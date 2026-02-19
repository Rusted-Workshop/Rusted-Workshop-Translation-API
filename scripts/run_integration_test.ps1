$ErrorActionPreference = "Stop"

Set-Location (Join-Path $PSScriptRoot "..")
$env:PYTHONUNBUFFERED = "1"

$logDir = Join-Path (Get-Location) "logs"
if (!(Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir | Out-Null
}

$apiOut = Join-Path $logDir "it-api.out.log"
$apiErr = Join-Path $logDir "it-api.err.log"
$coordOut = Join-Path $logDir "it-coordinator.out.log"
$coordErr = Join-Path $logDir "it-coordinator.err.log"
$fileOut = Join-Path $logDir "it-file-worker.out.log"
$fileErr = Join-Path $logDir "it-file-worker.err.log"

$api = $null
$coord = $null
$file = $null

try {
    $python = ".\.venv_clean\Scripts\python.exe"
    $api = Start-Process -FilePath $python -ArgumentList @("-m", "uvicorn", "api.main:app", "--host", "127.0.0.1", "--port", "8001", "--loop", "asyncio") -PassThru -RedirectStandardOutput $apiOut -RedirectStandardError $apiErr
    $coord = Start-Process -FilePath $python -ArgumentList @("-m", "workers.coordinator_worker") -PassThru -RedirectStandardOutput $coordOut -RedirectStandardError $coordErr
    $file = Start-Process -FilePath $python -ArgumentList @("-m", "workers.file_translation_worker") -PassThru -RedirectStandardOutput $fileOut -RedirectStandardError $fileErr

    $healthy = $false
    for ($i = 0; $i -lt 50; $i++) {
        try {
            $h = Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:8001/health" -TimeoutSec 3
            if ($h.status -eq "ok") {
                $healthy = $true
                break
            }
        } catch {
        }
        Start-Sleep -Seconds 1
    }

    if (-not $healthy) {
        throw "API did not become healthy in time."
    }

    $modPath = "C:\Users\d5v\Desktop\codes\Rusted-Workshop-Translation-API\tmp7cgm8rh1.rwmod"
    $uploadResp = curl.exe -s -X POST "http://127.0.0.1:8001/v1/tasks" -F "file=@$modPath" -F "target_language=zh-CN" -F "translate_style=auto"

    if ([string]::IsNullOrWhiteSpace($uploadResp)) {
        throw "Upload response is empty."
    }

    $taskObj = $uploadResp | ConvertFrom-Json
    $taskId = $taskObj.task_id
    if ([string]::IsNullOrWhiteSpace($taskId)) {
        throw "Upload response missing task_id: $uploadResp"
    }

    Write-Output "TASK_ID=$taskId"
    Write-Output "INITIAL_STATUS=$($taskObj.status)"

    $final = $null
    for ($i = 0; $i -lt 240; $i++) {
        Start-Sleep -Seconds 5
        $statusObj = Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:8001/v1/tasks/$taskId" -TimeoutSec 15
        Write-Output ("POLL {0}: status={1}, progress={2}, processed={3}/{4}" -f ($i + 1), $statusObj.status, $statusObj.progress, $statusObj.processed_files, $statusObj.total_files)

        if ($statusObj.status -eq "completed" -or $statusObj.status -eq "failed") {
            $final = $statusObj
            break
        }
    }

    if ($null -eq $final) {
        throw "Task did not reach terminal status within timeout."
    }

    Write-Output "FINAL_STATUS=$($final.status)"
    Write-Output "FINAL_PROGRESS=$($final.progress)"
    Write-Output "FINAL_ERROR=$($final.error_message)"

    if ($final.status -eq "completed") {
        $resultObj = Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:8001/v1/tasks/$taskId/result-url" -TimeoutSec 15
        Write-Output "RESULT_URL=$($resultObj.download_url)"
        Write-Output "RESULT_EXPIRES_IN=$($resultObj.expires_in)"
    }
} finally {
    foreach ($p in @($file, $coord, $api)) {
        if ($null -ne $p) {
            try {
                if (-not $p.HasExited) {
                    Stop-Process -Id $p.Id -Force
                }
            } catch {
            }
        }
    }
}
