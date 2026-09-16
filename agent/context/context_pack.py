# agent/context/context_pack.py
"""후보 Context 목록을 Hermes에 설치된 실제 context-pack Skill에 전달해
MUST_INCLUDE / USEFUL / CONFLICT / STALE / MISSING 분류와
최종 Handoff Context를 생성한다.

SKILL.md의 핵심 동작을 복제하지 않고, Hermes CLI로 context-pack Skill을 실제 호출한다.
"""
from __future__ import annotations
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any

from connectors import SearchResult


# context-pack에 넘길 후보 Context Item (retrieval 결과)
@dataclass
class CandidateContext:
    source: str
    title: str
    snippet: str
    ref: str
    timestamp: str | None = None
    primary_links: list[str] = field(default_factory=list)


@dataclass
class ContextPackResult:
    task_summary: str
    requirements_coverage: list[dict[str, Any]]      # requirement → FOUND/PARTIAL/MISSING
    canonical_items: list[dict[str, Any]]
    must_know: list[str]
    constraints: list[str]
    useful: list[str]
    unresolved_conflicts: list[dict[str, Any]]
    verify_before_use: list[dict[str, Any]]
    do_not_assume: list[str]
    source_map: list[str]
    handoff_text: str                             # 최종 복사 가능한 Context Bundle
    raw_hermes_output: str                        # trace용
    latency_ms: int = 0


#ContextPack Skill에 넘길 프롬프트.
# SKILL.md의 단계별 로직을 여기서 복제하지 않고, "ContextPack 역할을 맡아 이 후보들을 판단해라"라고 지시한다.
_CONTEXT_PACK_PROMPT = """\
당신은 **Context Pack Curator**입니다.
여러 협업 소스(GitHub, Jira, Slack, Notion)에서 수집된 후보 Context 목록이 주어집니다.
주어진 Role + Task를 기준으로, 이 후보들 중 **다음 Agent에게 실제로 넘겨야 할 정보**를 선별해 Context Pack을 구성하십시오.

## 금지
- 제공된 후보 목록 외의 사실을 만들어 넣지 마십시오.
- "검색됐으니까" 모두 넣지 마십시오. Inclusion Gate를 통과한 것만 포함하십시오.
- 충돌하는 정보를 임의로 하나로 합치거나 "최신일 것"으로 추정해 확정 사실로 만들지 마십시오.
- 원문의 확실성 수준(검토 중/예정/결정/미확정)을 바꾸지 마십시오.
- 원문에 없는 날짜/버전/숫자를 채우지 마십시오.

## 작업 순서
1. Task 이해: 아래 Role + Task를 파악하십시오.
2. 각 Candidate Context에 대해 "이 정보가 없으면 Task 수행에 실제로 문제가 되는가?"를 판단하십시오.
3. 정보를 다음 classification으로 분류하고, canonical item으로 정리하십시오.
   - MUST_INCLUDE
   - USEFUL
   - CONFLICT
   - STALE
   - MISSING
4. 중복 정보는 하나의 canonical item으로 합치고 source만 여러 개 연결하십시오.
5. 다음 형식의 Handoff Context를 작성하십시오. (이 형식이 핵심 최종 산출물입니다)

---
[TASK]
<업무를 한두 문장으로>

[MUST KNOW]
- <핵심 사실 1>
- <핵심 사실 2>

[CONSTRAINTS]
- <제약 1>
- <제약 2>

[USEFUL IF SPACE ALLOWS]
- <필수는 아니지만 도움 되는 정보>

[UNRESOLVED CONFLICTS]
- <충돌 A + source / 충돌 B + source / 충돌 설명>

[VERIFY BEFORE USE]
- <오래되었거나 최신 여부 불분명 정보 + 날짜/버전>

[DO NOT ASSUME]
- <추정해서는 안 되는 것 / MISSING 항목>

[SOURCE MAP]
- <핵심 사실 또는 context_id → 원본 출처(source + ref)>
---

## 입력
Role: <<ROLE>>
Task: <<TASK>>

## Candidate Context 목록
<<CANDIDATES>>

## 출력 규칙
- 먼저 아래에 Key-Value 구조의 JSON 블록 하나를 출력하십시오:
{
  "task_summary": "...",
  "requirements_coverage": [
    {"requirement": "...", "status": "FOUND | PARTIAL | MISSING", "evidence_refs": ["...", "..."]}
  ],
  "canonical_items": [
    {
      "context_id": "CTX-1",
      "requirement": "CR-x",
      "content": "...",
      "classification": "MUST_INCLUDE | USEFUL | CONFLICT | STALE | MISSING",
      "why_needed": "...",
      "source": ["source:ref", ...],
      "date_or_version": "...",
      "status": "FOUND | PARTIAL | MISSING"
    }
  ],
  "must_know": ["...", "..."],
  "constraints": ["...", "..."],
  "useful": ["...", "..."],
  "unresolved_conflicts": [
    {
      "claim_a": "...",
      "source_a": "...",
      "claim_b": "...",
      "source_b": "...",
      "conflict": "..."
    }
  ],
  "verify_before_use": [
    {"item": "...", "reason": "...", "date_or_version": "..."}
  ],
  "do_not_assume": ["...", "..."],
  "source_map": ["...", "..."]
}
- 그다음 위 Handoff Context 블록([TASK] ... [SOURCE MAP])을 그대로 출력하십시오.
- JSON과 Handoff Context 사이의 사이에 구분선으로 "---"를 한 줄 넣어 구분하십시오.
- JSON 출력 전에는 아무 텍스트도 넣지 마십시오.
"""


def _build_candidates_text(candidates: list[CandidateContext]) -> str:
    lines = []
    for c in candidates:
        lines.append(f"### {c.source}:{c.ref}")
        lines.append(f"- source: {c.source}")
        lines.append(f"- ref: {c.ref}")
        lines.append(f"- title: {c.title}")
        if c.timestamp:
            lines.append(f"- timestamp: {c.timestamp}")
        if c.primary_links:
            lines.append(f"- links: {', '.join(c.primary_links)}")
        lines.append(f"- snippet: {c.snippet}")
        lines.append("")
    return "\n".join(lines)


def _fill_prompt(role: str, task: str, candidates: list[CandidateContext]) -> str:
    candidates_text = _build_candidates_text(candidates)
    return (
        _CONTEXT_PACK_PROMPT
        .replace("<<ROLE>>", role)
        .replace("<<TASK>>", task)
        .replace("<<CANDIDATES>>", candidates_text)
    )


def run(
    role: str,
    task: str,
    candidates: list[CandidateContext],
    timeout_s: int = 120,
) -> ContextPackResult:
    prompt = _fill_prompt(role, task, candidates)
    hermes_exe = os.environ.get("HERMES_EXE", "hermes")
    cmd = [
        hermes_exe,
        "chat",
        "--quiet",
        "-q", prompt,
        "--skills", "context-pack",
    ]
    start = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, check=False)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"context-pack hermes call timed out after {timeout_s}s")
    latency_ms = int((time.time() - start) * 1000)
    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    if proc.returncode != 0:
        raise RuntimeError(f"context-pack hermes call failed (rc={proc.returncode}): {stderr[:500]}")

    json_block, handoff_text = _split_output(stdout)
    data = json.loads(json_block)

    return ContextPackResult(
        task_summary=data.get("task_summary", task),
        requirements_coverage=data.get("requirements_coverage", []),
        canonical_items=data.get("canonical_items", []),
        must_know=data.get("must_know", []),
        constraints=data.get("constraints", []),
        useful=data.get("useful", []),
        unresolved_conflicts=data.get("unresolved_conflicts", []),
        verify_before_use=data.get("verify_before_use", []),
        do_not_assume=data.get("do_not_assume", []),
        source_map=data.get("source_map", []),
        handoff_text=handoff_text,
        raw_hermes_output=stdout,
        latency_ms=latency_ms,
    )


def _split_output(stdout: str) -> tuple[str, str]:
    """Hermes 출력에서 JSON 블록과 그 뒤의 Handoff Context를 분리한다."""
    sep = "\n---\n"
    if sep in stdout:
        parts = stdout.split(sep, 1)
        return parts[0].strip(), parts[1].strip()
    json_block = _extract_json_block(stdout)
    return json_block, stdout


def _extract_json_block(text: str) -> str:
    depth = 0
    start = None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                return text[start:i + 1]
    raise ValueError(f"JSON block not found in context-pack output:\n{text[:1000]}")
