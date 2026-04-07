"""Tool registry for the standalone ReAct agent.

Each tool is a callable that takes string arguments and returns a string result.
Tools wrap real system operations (file read, shell, grep) for agent use.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict  # JSON schema for arguments

    def __call__(self, **kwargs: str) -> str:
        raise NotImplementedError


class FileReadTool(Tool):
    """Read a file and return its contents."""

    def __init__(self, workspace: str = ".") -> None:
        super().__init__(
            name="file_read",
            description="Read the contents of a file. Arguments: path (str).",
            parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        )
        self.workspace = workspace

    def __call__(self, **kwargs: str) -> str:
        path = kwargs.get("path", "")
        full_path = Path(self.workspace) / path
        try:
            content = full_path.read_text()
            if len(content) > 50_000:
                content = content[:50_000] + f"\n... [truncated, {len(content)} chars total]"
            return content
        except Exception as e:
            return f"Error reading {path}: {e}"


class ShellTool(Tool):
    """Execute a shell command and return stdout/stderr."""

    def __init__(self, workspace: str = ".", timeout: int = 30) -> None:
        super().__init__(
            name="bash",
            description="Execute a bash command. Arguments: command (str).",
            parameters={"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
        )
        self.workspace = workspace
        self.timeout = timeout

    def __call__(self, **kwargs: str) -> str:
        command = kwargs.get("command", "")
        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            output = result.stdout
            if result.stderr:
                output += f"\nSTDERR:\n{result.stderr}"
            if result.returncode != 0:
                output += f"\n[exit code: {result.returncode}]"
            if len(output) > 50_000:
                output = output[:50_000] + "\n... [truncated]"
            return output or "(no output)"
        except subprocess.TimeoutExpired:
            return f"Error: command timed out after {self.timeout}s"
        except Exception as e:
            return f"Error executing command: {e}"


class GrepTool(Tool):
    """Search for a pattern in files."""

    def __init__(self, workspace: str = ".") -> None:
        super().__init__(
            name="grep",
            description="Search for a regex pattern in files. Arguments: pattern (str), path (str, optional).",
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string", "default": "."},
                },
                "required": ["pattern"],
            },
        )
        self.workspace = workspace

    def __call__(self, **kwargs: str) -> str:
        pattern = kwargs.get("pattern", "")
        path = kwargs.get("path", ".")
        full_path = Path(self.workspace) / path
        try:
            result = subprocess.run(
                ["grep", "-rn", "--include=*.py", "--include=*.js", "--include=*.ts",
                 "--include=*.go", "--include=*.java", "--include=*.md",
                 pattern, str(full_path)],
                capture_output=True,
                text=True,
                timeout=15,
            )
            output = result.stdout
            if len(output) > 30_000:
                output = output[:30_000] + "\n... [truncated]"
            return output or "(no matches)"
        except Exception as e:
            return f"Error: {e}"


class FileWriteTool(Tool):
    """Write content to a file."""

    def __init__(self, workspace: str = ".") -> None:
        super().__init__(
            name="file_write",
            description="Write content to a file. Arguments: path (str), content (str).",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        )
        self.workspace = workspace

    def __call__(self, **kwargs: str) -> str:
        path = kwargs.get("path", "")
        content = kwargs.get("content", "")
        full_path = Path(self.workspace) / path
        try:
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content)
            return f"Written {len(content)} chars to {path}"
        except Exception as e:
            return f"Error writing {path}: {e}"


def default_tools(workspace: str = ".") -> dict[str, Tool]:
    """Create the default tool set for the ReAct agent."""
    return {
        "file_read": FileReadTool(workspace),
        "bash": ShellTool(workspace),
        "grep": GrepTool(workspace),
        "file_write": FileWriteTool(workspace),
    }
