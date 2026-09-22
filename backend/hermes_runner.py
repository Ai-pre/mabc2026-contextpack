import json
import os
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from backend.handoff_policy import sanitize_handoff


ANSI_RE = re.compile(
    r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])"
)


class HermesExecutionError(RuntimeError):
    pass


class HermesTimeoutError(TimeoutError):
    pass


@dataclass
class HermesRunResult:
    duration_sec: float
    handoff: str
    skill_used: bool
    mcp_tool_calls: int
    mcp_tools: list[str]
    retrieval_timing_ms: dict[str, float] = field(default_factory=dict)


class CliHermesRunner:
    def __init__(self, timeout_sec: int = 300):
        self.timeout_sec = timeout_sec
        self.max_turns = max(4, int(os.getenv("HERMES_MAX_TURNS", "8")))
        self.project_root = Path(__file__).resolve().parent

        self.hermes_bin = (
            os.getenv("HERMES_BIN")
            or shutil.which("hermes")
        )

        if not self.hermes_bin:
            raise RuntimeError(
                "hermes executable을 찾을 수 없습니다. "
                "PATH 또는 HERMES_BIN을 확인하세요."
            )

    def run(
        self,
        prompt: str,
        workspace_id: str | None = None,
        github_enabled: bool = False,
        preloaded_mcp_tools: list[str] | None = None,
        preloaded_retrieval_timing_ms: dict[str, float] | None = None,
    ) -> HermesRunResult:
        # Do not pass --toolsets here.
        #
        # Hermes 0.21 can fail to expose dynamically discovered stdio MCP tools
        # when an explicit dynamic MCP toolset is selected for a oneshot
        # session. The default CLI toolset path correctly injects configured
        # MCP tools after discovery. ContextPack constrains source use in the
        # prompt instead.
        cmd = [
            self.hermes_bin,
            "chat",
            "--oneshot",
            "--skills",
            "context-pack",
            "--max-turns",
            str(self.max_turns),
            "-q",
            prompt,
        ]

        env = os.environ.copy()
        run_id = uuid.uuid4().hex
        env["CONTEXTPACK_RUN_ID"] = run_id

        if workspace_id:
            env["CONTEXTPACK_WORKSPACE_ID"] = workspace_id
        if preloaded_mcp_tools:
            env["CONTEXTPACK_PRELOADED_EVIDENCE"] = "1"

        started = time.perf_counter()

        try:
            proc = subprocess.run(
                cmd,
                cwd=self.project_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_sec,
                shell=False,
                env=env,
            )

        except subprocess.TimeoutExpired as exc:
            raise HermesTimeoutError(
                f"Hermes execution timed out after "
                f"{self.timeout_sec}s"
            ) from exc

        finished = time.perf_counter()
        duration = finished - started
        retrieval_timing_ms = dict(preloaded_retrieval_timing_ms or {})
        if not retrieval_timing_ms:
            retrieval_timing_ms = self._read_retrieval_timing(
                run_id,
                run_started_perf=started,
                run_finished_perf=finished,
            )

        # 실제 Handoff 후보는 stdout에서 추출. Hermes may return code 1
        # after reaching its iteration budget even though a complete final
        # Handoff was already emitted.
        clean_stdout = self._clean_output(
            proc.stdout or ""
        )
        clean_combined = self._clean_output(
            (proc.stdout or "") + "\n" + (proc.stderr or "")
        )

        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()
            stdout = (proc.stdout or "").strip()
            budget_reached = bool(re.search(
                r"(?i)iteration budget reached|response may be incomplete",
                stdout + "\n" + stderr,
            ))
            recovered_handoff = ""
            if budget_reached:
                recovered_handoff = (
                    self._extract_handoff(clean_stdout)
                    or self._extract_handoff(clean_combined)
                )
            recovered_trace = clean_combined
            recovered_calls = self._count_mcp_calls(recovered_trace)
            preloaded_tools = list(preloaded_mcp_tools or [])
            effective_calls = recovered_calls + len(preloaded_tools)
            if recovered_handoff and effective_calls > 0:
                recovered_tools = self._extract_mcp_tools(
                    recovered_trace,
                    handoff=recovered_handoff,
                    retrieval_timing_ms=retrieval_timing_ms,
                )
                recovered_tools = list(dict.fromkeys(preloaded_tools + recovered_tools))
                return HermesRunResult(
                    duration_sec=round(duration, 2),
                    handoff=recovered_handoff,
                    skill_used=True,
                    mcp_tool_calls=effective_calls,
                    mcp_tools=recovered_tools,
                    retrieval_timing_ms=retrieval_timing_ms,
                )

            raise HermesExecutionError(
                f"Hermes exited with code "
                f"{proc.returncode}\n"
                f"stderr:\n{stderr[-3000:]}\n"
                f"stdout tail:\n{stdout[-1500:]}"
            )

        # MCP trace는 stdout / stderr 양쪽에서 나올 수 있음
        trace_text = clean_combined

        handoff = (
            self._extract_handoff(clean_stdout)
            or self._extract_handoff(clean_combined)
        )

        if not handoff:
            required_sections = [
                "[TASK]",
                "[MUST KNOW]",
                "[CONSTRAINTS]",
                "[USEFUL IF SPACE ALLOWS]",
                "[UNRESOLVED CONFLICTS]",
                "[VERIFY BEFORE USE]",
                "[DO NOT ASSUME]",
                "[SOURCE MAP]",
            ]
            detected = [
                section
                for section in required_sections
                if re.search(self._section_pattern(section), clean_stdout)
            ]
            missing = [
                section for section in required_sections
                if section not in detected
            ]
            detail = (
                " Missing sections: " + ", ".join(missing)
                if missing
                else " All section headers were detected; output contained invalid runtime/tool trace ordering."
            )
            tail = clean_combined[-1800:].strip()
            raise HermesExecutionError(
                "Hermes succeeded but Handoff Context could not be extracted."
                + detail
                + ("\nOutput tail:\n" + tail if tail else "")
            )

        # 이 값은 stdout에서 activation을 추측하는 값이 아니라
        # 실제 Hermes 실행 명령에 context-pack Skill이 enabled되어
        # 있는지를 나타낸다.
        skill_enabled = (
            "--skills" in cmd
            and "context-pack" in cmd
        )

        trace_calls = self._count_mcp_calls(trace_text)
        preloaded_tools = list(preloaded_mcp_tools or [])
        mcp_tool_calls = trace_calls + len(preloaded_tools)
        mcp_tools = self._extract_mcp_tools(
            trace_text,
            handoff=handoff,
            retrieval_timing_ms=retrieval_timing_ms,
        )
        mcp_tools = list(dict.fromkeys(preloaded_tools + mcp_tools))

        if mcp_tool_calls == 0:
            raise HermesExecutionError(
                "Hermes produced a Handoff without reading any registered Source. "
                "ContextPack refuses source-free handoffs; retry the analysis."
            )

        return HermesRunResult(
            duration_sec=round(duration, 2),
            handoff=handoff,
            skill_used=skill_enabled,
            mcp_tool_calls=mcp_tool_calls,
            mcp_tools=mcp_tools,
            retrieval_timing_ms=retrieval_timing_ms,
        )

    @staticmethod
    def _read_retrieval_timing(
        run_id: str,
        run_started_perf: float | None = None,
        run_finished_perf: float | None = None,
    ) -> dict[str, float]:
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", run_id):
            return {}
        path = Path("/tmp") / f"contextpack-retrieval-{run_id}.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return {}

            public = {
                str(key): round(float(value), 2)
                for key, value in raw.items()
                if not str(key).startswith("_")
                and isinstance(value, (int, float))
                and value >= 0
            }

            retrieval_started = raw.get("_retrieval_started_perf")
            retrieval_finished = raw.get("_retrieval_finished_perf")
            if (
                isinstance(run_started_perf, (int, float))
                and isinstance(run_finished_perf, (int, float))
                and isinstance(retrieval_started, (int, float))
                and isinstance(retrieval_finished, (int, float))
                and run_started_perf <= retrieval_started <= retrieval_finished <= run_finished_perf
            ):
                public["before_retrieval"] = round(
                    (retrieval_started - run_started_perf) * 1000,
                    2,
                )
                public["after_retrieval"] = round(
                    (run_finished_perf - retrieval_finished) * 1000,
                    2,
                )

            return public
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return {}
        finally:
            try:
                path.unlink()
            except OSError:
                pass

    @staticmethod
    def _clean_output(text: str) -> str:
        text = ANSI_RE.sub("", text)

        return (
            text
            .replace("\r\n", "\n")
            .replace("\r", "\n")
        )

    @staticmethod
    def _clean_cli_noise(text: str) -> str:
        """
        Hermes CLI 표시용 noise만 제거한다.
        Handoff 내용 자체는 생성하거나 수정하지 않는다.
        """

        # 예:
        # ... omitted 13 diff line(s) across ...
        text = re.sub(
            r"(?im)^"
            r"\s*\.\.\.\s*"
            r"omitted\s+\d+\s+diff line\(s\)"
            r".*$",
            "",
            text,
        )

        # standalone Hermes separator/signature
        #
        # — Hermes
        # ──── Hermes
        # ? Hermes
        text = re.sub(
            r"(?im)^\s*[-—─?]+\s*Hermes\s*$",
            "",
            text,
        )

        return text

    @staticmethod
    def _section_pattern(section: str) -> str:
        """Accept exact headers plus harmless Markdown/CLI decoration."""
        return (
            r"(?m)^\s*[│┃]?\s*"
            r"(?:#{1,6}\s*)?"
            r"(?:\*\*)?\s*"
            + re.escape(section)
            + r"\s*(?:\*\*)?\s*:?\s*$"
        )

    @staticmethod
    def _extract_mcp_tools(
        text: str,
        handoff: str = "",
        retrieval_timing_ms: dict[str, float] | None = None,
    ) -> list[str]:
        """Return tools from actual Hermes trace events.

        Never infer tools from the entire stdout because the echoed runtime
        prompt may itself contain MCP examples. If Hermes truncates a trace
        event to `mcp__mabc`, first use the retrieval timing side-channel when
        it proves the aggregate workspace path was executed; otherwise recover
        the leaf only from the already-extracted final Handoff SOURCE MAP.
        """
        trace = re.findall(
            r"(?mi)^.*⚡\s+(mcp[^\s(]+)",
            text,
        )
        trace = list(dict.fromkeys(name.rstrip(",:;") for name in trace))

        full_trace = [
            name for name in trace
            if re.match(r"(?i)^mcp__[A-Za-z0-9_]+__[A-Za-z0-9_]+$", name)
        ]
        if full_trace:
            return full_trace

        if retrieval_timing_ms and "aggregate" in retrieval_timing_ms:
            return ["mcp__mabc_sources__workspace_retrieve"]

        if handoff:
            full_handoff = re.findall(
                r"(?i)\b(mcp__[A-Za-z0-9_]+__[A-Za-z0-9_]+)\b",
                handoff,
            )
            if full_handoff:
                return list(dict.fromkeys(full_handoff))

            aggregate_leafs = re.findall(
                r"(?i)\b(workspace_retrieve|demo_context_retrieve|document_retrieve|github_retrieve|slack_retrieve|notion_retrieve)\b",
                handoff,
            )
            aggregate_leafs = list(dict.fromkeys(name.lower() for name in aggregate_leafs))
            if len(aggregate_leafs) == 1:
                return [f"mcp__mabc_sources__{aggregate_leafs[0]}"]

        return trace

    @staticmethod
    def _count_mcp_calls(text: str) -> int:
        """Count MCP trace events without double-counting truncated/full mirrors."""
        full = re.findall(
            r"(?mi)^.*⚡\s+mcp__[A-Za-z0-9_]+__[A-Za-z0-9_]+\b",
            text,
        )
        if full:
            return len(full)
        return len(
            re.findall(
                r"(?mi)^.*⚡\s+mcp(?:__|_)",
                text,
            )
        )

    @staticmethod
    def _sanitize_handoff(handoff: str) -> str:
        """Apply the shared connector-neutral Handoff policy."""
        return sanitize_handoff(handoff)

    @staticmethod
    def _is_placeholder_handoff(handoff: str) -> bool:
        """Reject a copied output template with no real task/evidence content."""
        placeholder_lines = re.findall(
            r"(?mi)^\s*-\s*(?:\.\.\.|source|<[^>]+>)\s*$",
            handoff,
        )
        return len(placeholder_lines) >= 3

    @staticmethod
    def _extract_handoff(text: str) -> str:
        """
        Hermes stdout에서 완성된 ContextPack Handoff만 추출한다.

        prompt echo / runtime trace / 불완전한 template은 제외하고,
        최종 Handoff 뒤에 붙는 Hermes session 안내는 잘라낸다.
        Hermes 0.21+가 Markdown heading/bold로 section header를 꾸며도
        동일한 Handoff contract로 정규화한다.
        """

        cleaned = CliHermesRunner._clean_cli_noise(text)

        required_sections = [
            "[TASK]",
            "[MUST KNOW]",
            "[CONSTRAINTS]",
            "[USEFUL IF SPACE ALLOWS]",
            "[UNRESOLVED CONFLICTS]",
            "[VERIFY BEFORE USE]",
            "[DO NOT ASSUME]",
            "[SOURCE MAP]",
        ]
        patterns = {
            section: CliHermesRunner._section_pattern(section)
            for section in required_sections
        }

        task_matches = list(
            re.finditer(
                patterns["[TASK]"],
                cleaned,
            )
        )

        if not task_matches:
            return ""

        # 가장 뒤쪽 [TASK]부터 검사한다.
        for index in range(len(task_matches) - 1, -1, -1):
            start = task_matches[index].start()

            if index + 1 < len(task_matches):
                end = task_matches[index + 1].start()
                candidate = cleaned[start:end].strip()
            else:
                candidate = cleaned[start:].strip()

            # 1. ContextPack 8개 section 존재 + 순서 확인
            positions = []
            valid = True

            for section in required_sections:
                match = re.search(
                    patterns[section],
                    candidate,
                )

                if not match:
                    valid = False
                    break

                positions.append(match.start())

            if not valid:
                continue

            if positions != sorted(positions):
                continue

            # 2. SOURCE MAP 이후는 Handoff가 끝난 뒤 붙는
            #    Hermes CLI commentary/session 영역일 수 있다.
            source_map_match = re.search(
                patterns["[SOURCE MAP]"],
                candidate,
            )

            if not source_map_match:
                continue

            search_from = source_map_match.end()
            remainder = candidate[search_from:]

            commentary_patterns = [
                # Hermes CLI closing / signature
                r"(?mi)^\s*[-—─?]+\s*Hermes\s*$",

                # Runtime/tool trace that may be printed after the final answer.
                # Match trace-shaped lines only; ordinary SOURCE MAP text is allowed
                # to mention names such as mcp__mabc_sources__demo_context_retrieve.
                r"(?mi)^\s*Initializing agent\.\.\..*$",
                r"(?mi)^\s*[│┃]?\s*[⚡🔧🧰🔍]\s+.*$",
                r"(?mi)^\s*[│┃]?\s*preparing(?:_|\s).*$",
                r"(?mi)^\s*Local tools require one entry per tool_call.*$",

                # 응답 뒤 commentary
                r"(?mi)^\s*ContextPack\s+(?:완성|complete|ready)\b.*$",
                r"(?mi)^\s*파일:.*$",
                r"(?mi)^\s*추가로 업로드\b.*$",

                # CLI session metadata
                r"(?mi)^\s*Resume this session with:.*$",
                r"(?mi)^\s*Session:.*$",
                r"(?mi)^\s*Title:.*$",
                r"(?mi)^\s*Duration:.*$",
                r"(?mi)^\s*Messages:.*$",
            ]

            cut_positions = []

            for pattern in commentary_patterns:
                match = re.search(
                    pattern,
                    remainder,
                )

                if match:
                    cut_positions.append(
                        search_from + match.start()
                    )

            if cut_positions:
                candidate = candidate[
                    :min(cut_positions)
                ].rstrip()

            # 3. 이제 Handoff 본문 내부에 runtime/tool trace가
            #    섞였는지만 검사한다.
            runtime_line_patterns = [
                r"(?mi)^\s*Initializing agent\.\.\..*$",
                r"(?mi)^\s*[│┃]?\s*[⚡🔧🧰🔍]\s+.*$",
                r"(?mi)^\s*[│┃]?\s*preparing(?:_|\s).*$",
                r"(?mi)^\s*\|\s*DSML\s*\|.*$",
                r"(?mi)^\s*Local tools require one entry per tool_call.*$",
            ]

            if any(
                re.search(pattern, candidate)
                for pattern in runtime_line_patterns
            ):
                continue

            # 4. Header decoration을 고정 Handoff contract로 정규화한다.
            for section in required_sections:
                candidate = re.sub(
                    patterns[section],
                    section,
                    candidate,
                )

            # 5. Hermes box border 제거
            lines = []

            for line in candidate.splitlines():
                stripped = line.lstrip()

                if stripped.startswith("╰"):
                    continue

                if stripped.startswith("╭"):
                    continue

                lines.append(line)

            result = "\n".join(lines).strip()

            if result and not CliHermesRunner._is_placeholder_handoff(result):
                return CliHermesRunner._sanitize_handoff(result)

        return ""