# agent/cli.py — MVP CLI entrypoint (개발 중)
from __future__ import annotations
import argparse
import json
import sys
import time
from typing import Any

from connectors.github.connector import GitHubConnector
from connectors.jira.connector import JiraConnector
from connectors.slack.connector import SlackConnector
from connectors.notion.connector import NotionConnector

from agent.router.router import RouterResult, run as router_run
from agent.context.context_pack import CandidateContext, ContextPackResult, run as cp_run

CONNECTORS = {
    "github": GitHubConnector(),
    "jira": JiraConnector(),
    "slack": SlackConnector(),
    "notion": NotionConnector(),
}


def _to_candidates(results: list) -> list[CandidateContext]:
    out: list[CandidateContext] = []
    for r in results:
        out.append(CandidateContext(
            source=r.source,
            title=r.title,
            snippet=r.snippet,
            ref=r.ref,
            timestamp=r.timestamp,
            primary_links=r.primary_links,
        ))
    return out


def run_e2e(role: str, task: str, verbose: bool = True) -> dict[str, Any]:
    trace: list[dict[str, Any]] = []
    t0 = time.time()

    # 1) Router: Context Requirement Map + Source plan
    if verbose:
        print("[1/4] Router: Role+Task 분석 → Context Requirement Map")
    router: RouterResult
    router = router_run(role, task)
    trace.append({
        "step": "router",
        "role": router.role,
        "task": router.task,
        "requirements": router.requirements,
        "source_plan": router.source_plan,
        "uncertainty_notes": router.uncertainty_notes,
        "latency_ms": router.latency_ms,
    })

    # 2) Source selection + retrieval
    if verbose:
        print("[2/4] Source connectors: Retrieval")
    all_candidates: list[CandidateContext] = []
    source_trace: list[dict[str, Any]] = []
    for plan in router.source_plan:
        src = plan["source"]
        connector = CONNECTORS.get(src)
        if connector is None:
            continue
        if verbose:
            print(f"  - {src}: {plan['reason']}  (query: {plan['query_hint']})")
        results = connector.search(plan["query_hint"], limit=20)
        candidates = _to_candidates(results)
        all_candidates.extend(candidates)
        source_trace.append({
            "source": src,
            "reason": plan["reason"],
            "query": plan["query_hint"],
            "hit_count": len(candidates),
            "items": [{"ref": c.ref, "title": c.title} for c in candidates],
        })
    trace.append({"step": "retrieval", "source_trace": source_trace})

    # 3) ContextPack Skill 호출
    if verbose:
        print("[3/4] ContextPack: 후보 Context 선별 → Handoff Context")
    cp: ContextPackResult
    cp = cp_run(role, task, all_candidates)
    trace.append({
        "step": "context_pack",
        "requirement_coverage": cp.requirements_coverage,
        "canonical_items_count": len(cp.canonical_items),
        "must_know_count": len(cp.must_know),
        "unresolved_conflicts_count": len(cp.unresolved_conflicts),
        "verify_before_use_count": len(cp.verify_before_use),
        "do_not_assume_count": len(cp.do_not_assume),
        "latency_ms": cp.latency_ms,
    })

    # 4) 최종 Handoff
    if verbose:
        print("[4/4] 최종 Handoff Context")
        print("=" * 70)
        print(cp.handoff_text)
        print("=" * 70)

    trace.append({
        "step": "handoff",
        "must_know": cp.must_know,
        "constraints": cp.constraints,
        "unresolved_conflicts": cp.unresolved_conflicts,
        "verify_before_use": cp.verify_before_use,
        "do_not_assume": cp.do_not_assume,
        "source_map": cp.source_map,
        "handoff_text": cp.handoff_text,
    })

    total_ms = int((time.time() - t0) * 1000)
    return {
        "role": role,
        "task": task,
        "trace": trace,
        "total_ms": total_ms,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="MABC 2026 MVP Agent CLI")
    p.add_argument("--role", default="Backend Developer")
    p.add_argument("--task", default="결제 모듈에 부분환불 기능 구현")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args(argv)

    result = run_e2e(args.role, args.task, verbose=not args.quiet)
    if args.quiet:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
