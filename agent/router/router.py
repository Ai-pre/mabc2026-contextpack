"""
Agent Router / Planner.

Role + Current Task → Context Requirement Map + Source 탐색 계획.
Solar Pro 4로 Hermes CLI subprocess (hermes chat -q)를 호출하여
모든 LLM 판단을 수행한다.

모델/provider 설정은 한 곳(config.yaml)에서 관리되며,
이 코드는 키를 직접 다루지 않는다 — Hermes가 .env에서 읽는다.
"""

from __future__ import annotations

import json
import subprocess
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 프로젝트 루트를 기준으로 상대 경로 계산
PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class ContextRequirement:
    """Step 1에서 생성하는 하나의 Context Requirement."""
    id: str
    description: str
    category: str  # e.g. "policy", "api_version", "state_model", "constraint"
    priority: str  # MUST / SHOULD / NICE


@dataclass
class SourcePlan:
    """어떤 소스를 어떤 순서로 왜 확인할지."""
    source: str
    reason: str
    query_focus: str
    priority: int  # 1 = first


@dataclass
class RouterOutput:
    role: str
    task: str
    requirements: list[ContextRequirement] = field(default_factory=list)
    source_plan: list[SourcePlan] = field(default_factory=list)
    reasoning: str = ""


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """
너는 MABC 2026 MVP의 Agent Router다.
역할: 사용자의 Role + Current Task를 이해한 뒤, 그 Task를 제대로 수행하기 위해
반드시 알아야 할 Context Requirement를 도출하고, 어떤 Source(GitHub/Jira/Slack/Notion)를
어떤 순서로 확인할지 계획을 수립한다.

중요 원칙:
- 단순 요약이 아니라 "Task 수행에 필요한 Context 요구사항"을 도출한다.
- 사용자의 Role + Current Task가 Context 선택의 기준이다.
- Source 선택은 단순 if/else 고정이 아니라, Task에 따라 LLM 판단으로 결정한다.
- 4개 소스(GitHub, Jira, Slack, Notion)가 모두 사용 가능하다.
- 어떤 소스를 먼저 봐야 하는지, 어떤 정보를 더 찾아야 하는지 판단한다.
""".strip()

ROUTER_USER_PROMPT_TEMPLATE = """
[Role]
{role}

[Current Task]
{task}

[사용 가능한 Source]
- GitHub (PR, 코드 파일, diff 요약)
- Jira (issue, 요구사항, 상태, 코멘트)
- Slack (채널 메시지, 회의 중 결정사항, 예외 정책)
- Notion (공식 문서, 아키텍처, 정책, 설계 문서)

[지시]
위 Role과 Current Task를 수행할 때 "지금 이 작업을 제대로 수행하기 위해
반드시 알아야 할 정보"를 Context Requirement로 정리하라.

출력은 반드시 아래 JSON 스키마를 따르는 단일 JSON 객체여야 한다.
다른 텍스트나 설명 없이 JSON만 출력한다.

JSON 스키마:
{{
  "role_confirmation": "사용자가 밝힌 Role 요약",
  "task_summary": "사용자가 수행할 Task를 한 문장으로",
  "requirements": [
    {{
      "id": "R1",
      "description": "무엇을 알아야 하는지",
      "category": "policy|api_version|state_model|constraint|exception|recent_changes|other",
      "priority": "MUST|SHOULD|NICE"
    }}
  ],
  "source_plan": [
    {{
      "source": "GitHub|Jira|Slack|Notion",
      "reason": "왜 이 소스를 확인해야 하는지",
      "query_focus": "이 소스에서 어떤 정보를 중점적으로 찾을지",
      "priority": 1
    }}
  ],
  "reasoning": "Role+Task에서 이런 Context Requirement가 도출된 이유"
}}

주의:
- requirements는 반드시 Task 수행에 실제로 필요한 것만 포함한다.
- source_plan은 simple if/else가 아니라 이 Task에 맞는 판단이어야 한다.
- "MUST" priority 항목은 downstream Agent가 해당 정보 없이 작업하면
  요구사항 위반이 발생할 수 있는 것들이다.
""".strip()


def _run_hermes_chat(query: str, skills: list[str] | None = None) -> str:
    """
    Hermes CLI subprocess로 Solar Pro 4에 질의한다.
    - model/provider는 config.yaml 설정을 따른다 (기본 solar-pro4 / upstage).
    - API key는 Hermes가 .env에서 읽는다. 이 코드는 키를 직접 다루지 않는다.
    - skills를 주면 --skills로 사전 로딩한다.
    """
    cmd = [str(_hermes_bin()), "chat", "-q", "--quiet"]
    if skills:
        cmd.extend(["--skills", ",".join(skills)])
    cmd.append(query)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
    combined = result.stdout + result.stderr
    if result.returncode != 0:
        raise RuntimeError(f"hermes chat failed (rc={result.returncode}): {combined[:500]}")
    return combined.strip()


def _hermes_bin() -> Path:
    """hermes CLI binary 경로."""
    # PATH에 있다고 가정하되, 없으면 프로젝트 기준 일반적인 위치도 확인
    for cand in [Path("/usr/bin/hermes"), Path("/c/Users/jaesa/AppData/Local/hermes/hermes.exe"),
                 Path.home() / ".hermes" / "hermes.exe"]:
        if cand.exists():
            return cand
    # PATH lookup
    import shutil
    exe = shutil.which("hermes")
    if exe:
        return Path(exe)
    raise RuntimeError("hermes CLI not found in PATH or common locations")


def run_router(role: str, task: str) -> RouterOutput:
    """
    Role + Task → Context Requirement Map + Source Plan.
    Solar Pro 4로 Hermes CLI subprocess를 통해 수행한다.
    """
    prompt = textwrap.fill(ROUTER_USER_PROMPT_TEMPLATE.format(role=role, task=task), width=100)
    raw = _run_hermes_chat(prompt)
    # JSON만 추출 (혹시 앞뒤에 다른 텍스트가 붙을 수 있으니 가장 바깥 JSON 객체 찾기)
    parsed = _extract_json(raw)
    if parsed is None:
        raise ValueError(f"Router output is not valid JSON. Raw:\n{raw[:1000]}")
    return RouterOutput(
        role=parsed.get("role_confirmation", role),
        task=parsed.get("task_summary", task),
        requirements=[
            ContextRequirement(
                id=r.get("id", f"R{len(requirements)+1}"),
                description=r.get("description", ""),
                category=r.get("category", "other"),
                priority=r.get("priority", "SHOULD"),
            )
            for r in parsed.get("requirements", [])
        ],
        source_plan=[
            SourcePlan(
                source=s.get("source", ""),
                reason=s.get("reason", ""),
                query_focus=s.get("query_focus", ""),
                priority=s.get("priority", 99),
            )
            for s in sorted(parsed.get("source_plan", []), key=lambda x: x.get("priority", 99))
        ],
        reasoning=parsed.get("reasoning", ""),
    )


def _extract_json(text: str) -> dict | None:
    """텍스트에서 가장 바깥의 JSON 객체를 찾는다."""
    import re
    # JSON 코드 블록에서 추출 시도
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # 직접 JSON 객체 찾기
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return None


# ---------------------------------------------------------------------------
# Demo: CLI에서 바로 테스트할 수 있는 엔트리 포인트
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="MABC Router smoke test")
    ap.add_argument("--role", default="Backend Developer")
    ap.add_argument("--task", default="결제 모듈에 부분환불 기능을 구현해야 한다. 3주 만에 프로젝트에 복귀했다.")
    args = ap.parse_args()

    print(f"[Router] Role: {args.role}")
    print(f"[Router] Task: {args.task}")
    print("-" * 60)
    out = run_router(args.role, args.task)
    print(f"role_confirmation: {out.role}")
    print(f"task_summary: {out.task}")
    print(f"reasoning: {out.reasoning}")
    print(f"requirements ({len(out.requirements)}):")
    for r in out.requirements:
        print(f"  - [{r.priority}] {r.id}: {r.description} (category={r.category})")
    print(f"source_plan ({len(out.source_plan)}):")
    for s in out.source_plan:
        print(f"  - {s.source} (priority={s.priority}): {s.reason} | focus: {s.query_focus}")
