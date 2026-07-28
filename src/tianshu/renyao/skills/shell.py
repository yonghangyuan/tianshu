"""Shell Skill — 安全命令执行（Docker 沙箱内）。"""

from .base import BaseSkill, SkillTool


class ShellSkill(BaseSkill):
    name = "shell"
    description = "安全执行系统命令（Docker 沙箱隔离）"
    trigram = "地"
    trigger_keywords = ["运行", "执行", "run", "shell", "命令"]

    def get_tools(self) -> list[SkillTool]:
        return [SkillTool(
            name="shell_exec",
            description="Execute a shell command in a sandboxed environment. Use for: python scripts, file operations, data processing. Do NOT use for: deleting system files, installing software.",
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to execute"},
                    "timeout": {"type": "integer", "description": "Timeout in seconds", "default": 30},
                },
                "required": ["command"],
            },
            handler=self._exec,
        )]

    async def _exec(self, command: str, timeout: int = 30) -> str:
        from ...diyao.sandbox import DockerSandbox
        sandbox = DockerSandbox()
        result = await sandbox.run(command, timeout=timeout)
        return result.stdout or result.stderr or f"exit={result.exit_code}"
