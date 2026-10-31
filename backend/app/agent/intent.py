"""Mat or Opt: whether a turn talks and explains, or works on the user's files.

MatOpt serves two kinds of request that want different agents:

``mat``  conversation and explanation — answer, derive, prove, compute and
         draw figures with Python, read the attached or workspace files the
         answer needs. It does not change the user's files, run shell
         commands, delegate to other agents or start background jobs.
``opt``  work on the user's files, folders and code — the full toolset:
         edit and write files, run commands, data-agent and sub-agent work.

The user picks one in the composer, or leaves it on ``auto`` and
``classify`` decides per turn. Codex keeps the same split as user-selected
collaboration modes (chat vs agent; ``collaboration-mode-templates``); MatOpt
adds the automatic choice because most of its requests are questions, and a
question should not be answered by an agent that may rewrite files.

The classifier is deliberately deterministic and cheap: it scores explicit
signals (a verb that changes something, aimed at a file, folder or code; a
path or a known workspace file; a code block with "fix this") against
explanation signals (a question, "explain", "prove", "plot"). When the
evidence is thin it keeps the previous turn's mode, and with no history it
chooses ``mat``, which can still compute, read and plot. A Mat turn chosen
automatically can still upgrade itself: the ``switch_to_opt`` tool moves the
rest of the turn to Opt when the work turns out to need file changes. A Mat
turn the user chose explicitly never upgrades.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Literal, Optional

from app.agent.events import ToolResult
from app.agent.tools.base import Tool, ToolContext

Mode = Literal["mat", "opt"]

# Tools a Mat turn never uses: they change files, run commands, delegate, or
# start work that outlives the answer.
MAT_EXCLUDED = frozenset({
    "write_file", "edit_file", "bash", "bash_poll", "data_agent",
    "spawn_agent", "wait_agent", "send_message", "followup_task", "interrupt_agent", "list_agents",
})

_CHANGE_VERBS = (
    r"edit|modify|change|update|fix|refactor|rewrite|rename|move|delete|remove|clean(?:\s*up)?|organi[sz]e|"
    r"convert|migrate|merge|split|sort|format|lint|debug|implement|add|create|generate|write|save|export|"
    r"install|run|execute|build|compile|test|deploy|commit|replace|patch|restructure|tidy|backup|archive|unzip|zip|"
    r"修改|更改|改|修复|重构|重写|重命名|移动|删除|整理|转换|合并|拆分|实现|添加|创建|生成|写入|保存|导出|安装|运行|执行|编译|测试"
)
_TARGETS = (
    r"file|files|folder|folders|directory|directories|dir|repo|repository|project|codebase|code|script|scripts|"
    r"module|function|class|notebook|spreadsheet|dataset|csv|excel|xlsx|json|yaml|config|readme|latex|tex|"
    r"workspace|pipeline|package|tests?|"
    r"文件|文件夹|目录|代码|脚本|项目|仓库|函数|模块|数据集|表格|笔记本"
)
_CHANGE = re.compile(rf"\b(?:{_CHANGE_VERBS})\b[^.?!\n]{{0,60}}\b(?:{_TARGETS})\b|(?:{_CHANGE_VERBS}).{{0,20}}(?:{_TARGETS})",
                     re.IGNORECASE)
_MY_ARTIFACT = re.compile(rf"\b(?:my|our|this|these|the attached|the uploaded)\s+(?:{_TARGETS})\b|我的(?:{_TARGETS})",
                          re.IGNORECASE)
_PATH = re.compile(r"(?:^|[\s`'\"(])(?:[\w.-]+/)+[\w.-]+|(?:^|[\s`'\"(])[\w-]+\.(?:py|ipynb|csv|tsv|xlsx?|json|ya?ml|"
                   r"toml|md|tex|txt|js|ts|tsx|jsx|rs|go|java|c|cpp|h|r|m|sql|sh|docx|pptx|pdf)\b", re.IGNORECASE)
_CODE_FIX = re.compile(r"```.*?```", re.DOTALL)
_FIX_WORDS = re.compile(r"\b(fix|debug|refactor|optimi[sz]e|clean up|rewrite|bug|error|traceback)\b|修复|报错|错误",
                        re.IGNORECASE)
_EXPLAIN = re.compile(
    r"^\s*(what|why|how|when|where|who|which|is|are|can|could|does|do|should|would|explain|describe|define|prove|"
    r"derive|show that|tell me|compare|summari[sz]e|intuition|help me understand)\b|\b(explain|intuition|"
    r"meaning of|difference between|proof|derivation|plot|graph|visuali[sz]e|illustrate|figure|diagram|"
    r"what does|why does|how does|how do)\b|[?？]\s*$|^(什么|为什么|怎么|如何|解释|证明|推导|比较|画|讲讲|介绍)",
    re.IGNORECASE)
_GREETING = re.compile(r"^\s*(hi|hello|hey|thanks|thank you|ok|okay|good (morning|afternoon|evening)|你好|谢谢)\b",
                       re.IGNORECASE)


@dataclass(frozen=True)
class IntentDecision:
    mode: Mode
    source: Literal["user", "auto"]
    reason: str
    confidence: float = 1.0
    switched_from: Optional[Mode] = None   # set when an auto Mat turn upgraded itself

    def public(self) -> dict:
        out = {"mode": self.mode, "source": self.source, "reason": self.reason}
        if self.switched_from:
            out.update(switched=True, **{"from": self.switched_from})
        return out


def classify(text: str, *, attachments: Iterable[dict] = (), workspace_files: Iterable[str] = (),
             previous: Optional[Mode] = None) -> IntentDecision:
    """Choose Mat or Opt for an automatic turn."""
    text = text or ""
    opt, mat, why_opt, why_mat = 0.0, 0.0, [], []
    if _CHANGE.search(text):
        opt += 2
        why_opt.append("asks to change files or code")
    if _MY_ARTIFACT.search(text):
        opt += 1
        why_opt.append("refers to the user's own files or code")
    names = {str(f).rsplit("/", 1)[-1].lower() for f in workspace_files if f}
    lowered = text.lower()
    if _PATH.search(text) or any(name and name in lowered for name in names):
        opt += 1
        why_opt.append("names a file or path")
    if _CODE_FIX.search(text) and _FIX_WORDS.search(text):
        opt += 2
        why_opt.append("asks to fix pasted code")
    if _EXPLAIN.search(text.strip()):
        mat += 1.5
        why_mat.append("asks a question or for an explanation")
    if _GREETING.search(text):
        mat += 2
        why_mat.append("is conversation")
    if attachments and not opt:
        why_mat.append("asks about attached files")
    margin = opt - mat
    if margin >= 1:
        return IntentDecision("opt", "auto", "; ".join(why_opt), min(1.0, 0.5 + margin / 4))
    if margin <= -1 or (mat and not opt):
        return IntentDecision("mat", "auto", "; ".join(why_mat) or "conversation", min(1.0, 0.5 - margin / 4))
    if previous is not None:
        return IntentDecision(previous, "auto", "continues the previous turn's mode", 0.5)
    if opt:
        return IntentDecision("opt", "auto", "; ".join(why_opt), 0.5)
    return IntentDecision("mat", "auto", "no request to change files", 0.5)


def decide(requested: Optional[str], text: str, *, attachments: Iterable[dict] = (),
           workspace_files: Iterable[str] = (), previous: Optional[Mode] = None) -> IntentDecision:
    if requested in ("mat", "opt"):
        return IntentDecision(requested, "user", "chosen in the composer")
    return classify(text, attachments=attachments, workspace_files=workspace_files, previous=previous)


def mode_prompt(decision: IntentDecision, *, can_switch: bool) -> str:
    """The turn-context block that tells the model which mode it is in."""
    if decision.mode == "opt":
        return ("# Mode: Opt (work on files)\nThe user wants work done on their files, folders or code. Make the "
                "changes in the workspace, verify them, and end with what changed and where.\n")
    escape = ("If the request turns out to need changing the user's files or running commands, call "
              "`switch_to_opt` with the reason, then continue." if can_switch else
              "The user chose Mat: if the request needs changing their files or running commands, say so and "
              "suggest switching to Opt, instead of doing it.")
    return ("# Mode: Mat (chat and explain)\nAnswer, explain, derive and compute. You may run Python for "
            "calculations and figures and read files the answer needs, but do not modify the user's files or run "
            f"shell commands. {escape}\n")


class SwitchToOptTool(Tool):
    """Upgrade an automatically chosen Mat turn to Opt for its remaining steps."""

    name = "switch_to_opt"
    side_effect_scope = "none"
    risk_level = "low"
    requires_flags = ("mode_can_switch",)
    description = ("Switch this turn from Mat (chat and explain) to Opt (work on files) when the request actually "
                   "needs changing the user's files, folders or code, or running commands. File and shell tools "
                   "become available on the next step.")
    parameters = {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]}

    async def run(self, ctx: ToolContext, reason: str, **_: Any) -> ToolResult:
        if not ctx.state.get("mode_can_switch"):
            return ToolResult(ok=False, error="This turn's mode was chosen by the user and cannot change.")
        ctx.state["mode"] = "opt"
        ctx.state["mode_excluded"] = frozenset()
        ctx.state["mode_can_switch"] = False
        switch = ctx.state.get("mode_switched")
        if callable(switch):
            await switch(str(reason or "")[:300])
        return ToolResult(ok=True, output="Switched to Opt: file and shell tools are available from the next step.",
                          meta={"refresh_tools": True, "mode": "opt"})
