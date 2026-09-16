$payload = @{
    role = "3주 만에 프로젝트에 복귀한 백엔드 개발자"
    task = "결제 모듈의 부분환불 기능 수정"
} | ConvertTo-Json -Compress

Add-Type -AssemblyName System.Net.Http

$client = [System.Net.Http.HttpClient]::new()
$client.Timeout = [TimeSpan]::FromSeconds(120)

$content = [System.Net.Http.StringContent]::new(
    $payload,
    [System.Text.Encoding]::UTF8,
    "application/json"
)

Write-Host "POST /analyze ..."
$response = $client.PostAsync(
    "http://127.0.0.1:8000/analyze",
    $content
).GetAwaiter().GetResult()

$body = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()

Write-Host "STATUS: $([int]$response.StatusCode) $response.StatusCode"
Write-Host "BODY:"
Write-Host $body

$client.Dispose()
