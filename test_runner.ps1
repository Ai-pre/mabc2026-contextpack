$ErrorActionPreference = "Stop"

$hermesBin = "C:\Users\jaesa\AppData\Local\hermes\hermes-agent\venv\Scripts\hermes"
$projectRoot = "C:\Users\jaesa\mabc2026-mvp"
$prompt = "나는 3주 만에 프로젝트에 복귀한 백엔드 개발자다.`n결제 모듈의 부분환불 기능 수정`n현재 작업에 필요한 정보를 등록된 mabc-sources MCP에서 직접 탐색하고,`n설치된 context-pack Skill을 사용해 최종 Handoff Context를 만들어라."

$cmd = @(
    $hermesBin,
    "chat",
    "--oneshot",
    "--skills",
    "context-pack",
    "-q",
    $prompt
)

Write-Host "CMD: $($cmd -join ' ')"
Write-Host "CWD: $projectRoot"
Write-Host "START: $(Get-Date)"

$sw = [System.Diagnostics.Stopwatch]::StartNew()

$proc = [System.Diagnostics.Process]::Start($cmd[0], ($cmd | Select-Object -Skip 1) -join " ")

# stdout/stderr 리디렉션
$stdoutBuilder = New-Object System.Text.StringBuilder
$stderrBuilder = New-Object System.Text.StringBuilder

Register-ObjectEvent -InputObject $proc -EventName OutputDataReceived -Action {
    $builder = $using:stdoutBuilder
    if ($EventArgs.Data) {
        [void]$builder.AppendLine($EventArgs.Data)
    }
} | Out-Null

Register-ObjectEvent -InputObject $proc -EventName ErrorDataReceived -Action {
    $builder = $using:stderrBuilder
    if ($EventArgs.Data) {
        [void]$builder.AppendLine($EventArgs.Data)
    }
} | Out-Null

$proc.Start()
$proc.BeginOutputReadLine()
$proc.BeginErrorReadLine()

$finished = $proc.WaitForExit(280000)  # 280초
$sw.Stop()

Write-Host "EXIT: $($proc.ExitCode) (finished=$finished, elapsed=$($sw.Elapsed.TotalSeconds)s)"
Write-Host "STDOUT:"
Write-Host $stdoutBuilder.ToString()
Write-Host "STDERR:"
Write-Host $stderrBuilder.ToString()

if (-not $finished) {
    Write-Host "TIMEOUT: 프로세스 종료 대기 초과"
    $proc.Kill()
}
