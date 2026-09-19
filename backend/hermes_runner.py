import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


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


class CliHermesRunner:
    def __init__(self, timeout_sec: int = 300):
        self.timeout_sec = timeout_sec
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
    ) -> HermesRunResult:
        toolsets = ["skills", "mcp-mabc-sources"]
        if github_enabled:
            toolsets.append("mcp-github-live")

        cmd = [
            self.hermes_bin,
            "chat",
            "--oneshot",
            "--skills",
            "context-pack",
            "--toolsets",
            ",".join(toolsets),
            "--max-turns",
            "5",
            "-q",
            prompt,
        ]

        env = os.environ.copy()

        if workspace_id:
            env["CONTEXTPACK_WORKSPACE_ID"] = workspace_id

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

        duration = time.perf_counter() - started

        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()
            stdout = (proc.stdout or "").strip()

            raise HermesExecutionError(
                f"Hermes exited with code "
                f"{proc.returncode}\n"
                f"stderr:\n{stderr[-3000:]}\n"
                f"stdout tail:\n{stdout[-1500:]}"
            )

        # 실제 Handoff 후보는 stdout에서 추출
        clean_stdout = self._clean_output(
            proc.stdout or ""
        )

        # MCP trace는 stdout / stderr 양쪽에서 나올 수 있음
        trace_text = self._clean_output(
            (proc.stdout or "")
            + "\n"
            + (proc.stderr or "")
        )

        handoff = self._extract_handoff(
            clean_stdout
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
            raise HermesExecutionError(
                "Hermes succeeded but Handoff Context could not be extracted."
                + detail
            )

        # 이 값은 stdout에서 activation을 추측하는 값이 아니라
        # 실제 Hermes 실행 명령에 context-pack Skill이 enabled되어
        # 있는지를 나타낸다.
        skill_enabled = (
            "--skills" in cmd
            and "context-pack" in cmd
        )

        return HermesRunResult(
            duration_sec=round(duration, 2),
            handoff=handoff,
            skill_used=skill_enabled,
            mcp_tool_calls=self._count_mcp_calls(
                trace_text
            ),
        )

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
    def _count_mcp_calls(text: str) -> int:
        """
        Hermes CLI에서 관측 가능한 mabc MCP tool event를 센다.
        """

        return len(
            re.findall(
                r"(?mi)^.*⚡\s+mcp__",
                text,
            )
        )

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
            runtime_markers = [
                "Initializing agent",
                "preparing_tool_call",
                "preparing tool_call",
                "| DSML |",
                "mcp__mabc",
                "Local tools require one entry per tool_call",
            ]

            lower_candidate = candidate.lower()

            if any(
                marker.lower() in lower_candidate
                for marker in runtime_markers
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

            if result:
                return result

        return ""