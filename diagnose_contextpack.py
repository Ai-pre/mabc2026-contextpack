import subprocess
import time

prompt = """나는 3주 만에 프로젝트에 복귀한 백엔드 개발자다.
결제 모듈의 부분환불 기능을 수정해야 한다.
현재 작업에 필요한 정보를 등록된 mabc-sources MCP에서 직접 탐색하고,
설치된 context-pack Skill을 사용해 최종 Handoff Context를 만들어라."""

cmd = [
    "hermes",
    "chat",
    "--oneshot",
    "--skills",
    "context-pack",
    "-q",
    prompt,
]

print("=== PROMPT ===")
print(prompt)

print("\n=== COMMAND ===")
print(cmd)

start = time.time()

try:
    r = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=180,
        encoding="utf-8",
        errors="replace",
    )

    print("\nEXIT:", r.returncode)
    print("TIME:", round(time.time() - start, 1), "sec")

    print("\n=== STDOUT ===")
    print(r.stdout)

    print("\n=== STDERR ===")
    print(r.stderr)

except subprocess.TimeoutExpired as e:
    print("\nTIMEOUT:", round(time.time() - start, 1), "sec")

    out = e.stdout or ""
    if isinstance(out, bytes):
        out = out.decode("utf-8", errors="replace")

    err = e.stderr or ""
    if isinstance(err, bytes):
        err = err.decode("utf-8", errors="replace")

    print("\n=== PARTIAL STDOUT ===")
    print(out)

    print("\n=== PARTIAL STDERR ===")
    print(err)
