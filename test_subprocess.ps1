$ErrorActionPreference = "Stop"

$hermesBin = "C:\Users\jaesa\AppData\Local\hermes\hermes-agent\venv\Scripts\hermes"
$projectRoot = "C:\Users\jaesa\mabc2026-mvp"
$outFile = Join-Path $projectRoot "cli_test_out.txt"
$errFile = Join-Path $projectRoot "cli_test_err.txt"
$prompt = "나는 3주 만에 프로젝트에 복귀한 백엔드 개발자다.`n결제 모듈의 부분환불 기능 수정`n현재 작업에 필요한 정보를 등록된 mabc-sources MCP에서 직접 탐색하고,`n설치된 context-pack Skill을 사용해 최종 Handoff Context를 만들어라."

$start = Get-Date
Write-Host "START: $($start.ToString('HH:mm:ss.fff'))"
Write-Host "CMD: $hermesBin chat --oneshot --skills context-pack -q `"$($prompt -replace '`n', '\n')`""
Write-Host "CWD: $projectRoot"

$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = $hermesBin
$psi.Arguments = "chat --oneshot --skills context-pack -q `"$prompt`""
$psi.WorkingDirectory = $projectRoot
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $true

$proc = New-Object System.Diagnostics.Process
$proc.StartInfo = $psi

$outBuilder = New-Object System.Text.StringBuilder
$errBuilder = New-Object System.Text.StringBuilder

$outEvent = Register-ObjectEvent -InputObject $proc -EventName OutputDataReceived -Action {
    if ($EventArgs.Data) { $using:outBuilder.AppendLine($EventArgs.Data) }
} -MaxEnqueueEventPerSecond 1000

$errEvent = Register-ObjectEvent -InputObject $proc -EventName ErrorDataReceived -Action {
    if ($EventArgs.Data) { $using:errBuilder.AppendLine($EventArgs.Data) }
} -MaxEnqueueEventPerSecond 1000

$proc.Start() | Out-Null
$proc.BeginOutputReadLine()
$proc.BeginErrorReadLine()

$finished = $proc.WaitForExit(120000)  # 120초
$end = Get-Date
$elapsed = ($end - $start).TotalSeconds

Write-Host "EXIT: $($proc.ExitCode) (finished=$finished, elapsed=$elapsed sek)"
Write-Host "STDOUT:"
Write-Host $outBuilder.ToString()
Write-Host "STDERR:"
Write-Host $errBuilder.ToString()

# 파일에도 저장
$outBuilder.ToString() | Out-File -FilePath $outFile -Encoding UTF8
$errBuilder.ToString() | Out-File -FilePath $errFile -Encoding UTF8

if (-not $finished) {
    Write-Host "TIMEOUT: 120초 초과 → 프로세스 Kill"
    $proc.Kill()
    $proc.WaitForExit(5000) | Out-Null
}

Unregister-Event -SourceIdentifier $outEvent.Name -Force -ErrorAction SilentlyContinue
Unregister-Event -SourceIdentifier $errEvent.Name -ErrorAction SilentlyContinue
