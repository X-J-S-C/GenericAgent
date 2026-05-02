"""GenericAgent 工具适配器 - 保留 GA 的原子工具"""

from typing import Any, Dict, List, Optional
import os
import sys
import re
import json
import tempfile
import subprocess
import threading
import time
from dataclasses import dataclass

from oak_gkernel.core.tools import (
    BaseTool,
    ToolSchema,
    ToolResult,
    ToolCategory,
    ToolPort,
    ToolRegistry,
)


class GenericAgentToolRegistry(ToolRegistry):
    """GenericAgent 工具注册表
    
    预注册 GenericAgent 的 9 个原子工具。
    """
    
    def __init__(self):
        super().__init__()
        self._register_ga_tools()
    
    def _register_ga_tools(self) -> None:
        """注册 GenericAgent 原子工具"""
        tools = [
            CodeRunTool(),
            FileReadTool(),
            FileWriteTool(),
            FilePatchTool(),
            WebScanTool(),
            WebExecuteJSTool(),
            AskUserTool(),
            UpdateWorkingCheckpointTool(),
            StartLongTermUpdateTool(),
        ]
        
        for tool in tools:
            self.register(tool)


@dataclass
class CodeRunTool(BaseTool):
    """代码执行工具 - 封装 GenericAgent 的 code_run"""
    
    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="code_run",
            description="Execute Python code or shell commands. Python code runs as scripts, shell commands execute directly.",
            parameters={
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Code to execute"},
                    "type": {"type": "string", "description": "Code type: python, bash, powershell", "default": "python"},
                    "timeout": {"type": "integer", "description": "Timeout in seconds", "default": 60},
                    "cwd": {"type": "string", "description": "Working directory"},
                },
                "required": ["code"]
            },
            category=ToolCategory.CODE,
            tags=["execution", "script", "subprocess"],
        )
    
    async def execute(self, **kwargs) -> ToolResult:
        import time
        start = time.time()
        
        code = kwargs.get("code")
        code_type = kwargs.get("type", "python")
        timeout = kwargs.get("timeout", 60)
        cwd = kwargs.get("cwd", os.path.join(os.getcwd(), 'temp'))
        
        if not code:
            return ToolResult(success=False, error="Code is required")
        
        try:
            result = yield from self._run_code(code, code_type, timeout, cwd)
            return ToolResult(
                success=True,
                data=result,
                execution_time_ms=(time.time() - start) * 1000
            )
        except Exception as e:
            return ToolResult(
                success=False,
                error=str(e),
                execution_time_ms=(time.time() - start) * 1000
            )
    
    def _run_code(self, code: str, code_type: str, timeout: int, cwd: str):
        """执行代码"""
        tmp_path = None
        
        if code_type in ("python", "py"):
            tmp_file = tempfile.NamedTemporaryFile(
                suffix=".ai.py", 
                delete=False, 
                mode='w', 
                encoding='utf-8'
            )
            
            header_path = os.path.join(os.path.dirname(__file__), '../../assets/code_run_header.py')
            if os.path.exists(header_path):
                tmp_file.write(open(header_path, encoding='utf-8').read())
            
            tmp_file.write(code)
            tmp_path = tmp_file.name
            tmp_file.close()
            
            cmd = [sys.executable, "-X", "utf8", "-u", tmp_path]
        else:
            if os.name == 'nt':
                cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command", code]
            else:
                cmd = ["bash", "-c", code]
        
        full_stdout = []
        
        def stream_reader(proc, logs):
            try:
                for line in iter(proc.stdout.readline, b''):
                    try:
                        line = line.decode('utf-8')
                    except UnicodeDecodeError:
                        line = line.decode('gbk', errors='ignore')
                    logs.append(line)
            except:
                pass
        
        try:
            startupinfo = None
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
                cwd=cwd,
                startupinfo=startupinfo
            )
            
            t = threading.Thread(target=stream_reader, args=(process, full_stdout), daemon=True)
            t.start()
            
            start_t = time.time()
            while t.is_alive():
                if time.time() - start_t > timeout:
                    process.kill()
                    return {"status": "timeout", "stdout": "".join(full_stdout)}
                time.sleep(0.5)
            
            t.join(timeout=1)
            stdout_str = "".join(full_stdout)
            
            return {
                "status": "success" if process.poll() == 0 else "error",
                "stdout": stdout_str,
                "exit_code": process.poll()
            }
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)


@dataclass
class FileReadTool(BaseTool):
    """文件读取工具"""
    
    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="file_read",
            description="Read file content with optional line range and keyword search",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                    "start": {"type": "integer", "description": "Start line number", "default": 1},
                    "count": {"type": "integer", "description": "Number of lines to read", "default": 200},
                    "keyword": {"type": "string", "description": "Search keyword"},
                    "show_linenos": {"type": "boolean", "description": "Show line numbers", "default": True},
                },
                "required": ["path"]
            },
            category=ToolCategory.FILE,
            tags=["read", "file", "io"],
        )
    
    async def execute(self, **kwargs) -> ToolResult:
        path = kwargs.get("path")
        start = kwargs.get("start", 1)
        count = kwargs.get("count", 200)
        keyword = kwargs.get("keyword")
        show_linenos = kwargs.get("show_linenos", True)
        
        if not path:
            return ToolResult(success=False, error="Path is required")
        
        try:
            abs_path = os.path.abspath(path)
            with open(abs_path, 'r', encoding='utf-8', errors='replace') as f:
                lines = f.readlines()
            
            total_lines = len(lines)
            start_idx = max(0, start - 1)
            end_idx = min(len(lines), start_idx + count)
            
            content_lines = lines[start_idx:end_idx]
            
            if keyword:
                keyword = keyword.lower()
                found = False
                for i, line in enumerate(lines):
                    if keyword in line.lower():
                        found = True
                        start_idx = max(0, i - count // 3)
                        end_idx = min(len(lines), i + count // 3 + 1)
                        content_lines = lines[start_idx:end_idx]
                        break
                
                if not found:
                    return ToolResult(
                        success=True,
                        data=f"Keyword '{keyword}' not found in file"
                    )
            
            result = []
            for i, line in enumerate(content_lines, start=start_idx + 1):
                if show_linenos:
                    result.append(f"{i}|{line.rstrip()}")
                else:
                    result.append(line.rstrip())
            
            remaining = total_lines - end_idx
            header = f"[FILE] {total_lines} lines | Showing {start_idx+1}-{end_idx}"
            if remaining > 0:
                header += f" (+ {remaining} more)"
            
            return ToolResult(
                success=True,
                data=header + "\n" + "\n".join(result)
            )
            
        except FileNotFoundError:
            return ToolResult(success=False, error=f"File not found: {path}")
        except Exception as e:
            return ToolResult(success=False, error=str(e))


@dataclass
class FileWriteTool(BaseTool):
    """文件写入工具"""
    
    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="file_write",
            description="Write content to file. Use mode 'overwrite' to replace, 'append' to add.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                    "content": {"type": "string", "description": "Content to write"},
                    "mode": {"type": "string", "description": "Write mode: overwrite, append, prepend", "default": "overwrite"},
                },
                "required": ["path", "content"]
            },
            category=ToolCategory.FILE,
            tags=["write", "file", "io"],
        )
    
    async def execute(self, **kwargs) -> ToolResult:
        path = kwargs.get("path")
        content = kwargs.get("content", "")
        mode = kwargs.get("mode", "overwrite")
        
        if not path:
            return ToolResult(success=False, error="Path is required")
        
        try:
            abs_path = os.path.abspath(path)
            
            if mode == "append":
                with open(abs_path, 'a', encoding='utf-8') as f:
                    f.write(content)
            elif mode == "prepend":
                with open(abs_path, 'r', encoding='utf-8') as f:
                    existing = f.read()
                with open(abs_path, 'w', encoding='utf-8') as f:
                    f.write(content + existing)
            else:
                os.makedirs(os.path.dirname(abs_path), exist_ok=True)
                with open(abs_path, 'w', encoding='utf-8') as f:
                    f.write(content)
            
            return ToolResult(
                success=True,
                data={"bytes_written": len(content), "path": abs_path}
            )
            
        except Exception as e:
            return ToolResult(success=False, error=str(e))


@dataclass
class FilePatchTool(BaseTool):
    """文件打补丁工具 - 精确替换"""
    
    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="file_patch",
            description="Patch a file by replacing old_content with new_content",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                    "old_content": {"type": "string", "description": "Content to replace"},
                    "new_content": {"type": "string", "description": "New content"},
                },
                "required": ["path", "old_content", "new_content"]
            },
            category=ToolCategory.FILE,
            tags=["patch", "edit", "replace"],
        )
    
    async def execute(self, **kwargs) -> ToolResult:
        path = kwargs.get("path")
        old_content = kwargs.get("old_content", "")
        new_content = kwargs.get("new_content", "")
        
        if not all([path, old_content, new_content]):
            return ToolResult(success=False, error="path, old_content, and new_content are required")
        
        try:
            abs_path = os.path.abspath(path)
            
            if not os.path.exists(abs_path):
                return ToolResult(success=False, error=f"File not found: {path}")
            
            with open(abs_path, 'r', encoding='utf-8') as f:
                full_text = f.read()
            
            if old_content not in full_text:
                return ToolResult(success=False, error="old_content not found in file")
            
            updated_text = full_text.replace(old_content, new_content)
            
            with open(abs_path, 'w', encoding='utf-8') as f:
                f.write(updated_text)
            
            return ToolResult(
                success=True,
                data={"msg": "File patched successfully"}
            )
            
        except Exception as e:
            return ToolResult(success=False, error=str(e))


@dataclass
class WebScanTool(BaseTool):
    """网页扫描工具"""
    
    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="web_scan",
            description="Get simplified HTML content and tab list from browser",
            parameters={
                "type": "object",
                "properties": {
                    "tabs_only": {"type": "boolean", "description": "Only return tab list", "default": False},
                    "switch_tab_id": {"type": "string", "description": "Tab ID to switch to"},
                    "text_only": {"type": "boolean", "description": "Return text only without HTML", "default": False},
                },
            },
            category=ToolCategory.WEB,
            tags=["browser", "html", "scan"],
        )
    
    async def execute(self, **kwargs) -> ToolResult:
        try:
            # 尝试导入 GenericAgent 的 TMWebDriver
            try:
                from TMWebDriver import TMWebDriver
                driver = TMWebDriver()
            except ImportError:
                return ToolResult(
                    success=False, 
                    error="Browser driver not available. Install TMWebDriver extension."
                )
            
            tabs_only = kwargs.get("tabs_only", False)
            switch_tab_id = kwargs.get("switch_tab_id")
            text_only = kwargs.get("text_only", False)
            
            if switch_tab_id:
                driver.default_session_id = switch_tab_id
            
            if len(driver.get_all_sessions()) == 0:
                return ToolResult(success=False, error="No browser tabs available")
            
            tabs = []
            for sess in driver.get_all_sessions():
                tabs.append({
                    "id": sess.get('id'),
                    "url": sess.get('url', '')[:100],
                    "title": sess.get('title', ''),
                })
            
            result = {
                "status": "success",
                "tabs_count": len(tabs),
                "tabs": tabs,
                "active_tab": driver.default_session_id,
            }
            
            if not tabs_only:
                import simphtml
                importlib.reload(simphtml)
                html_content = simphtml.get_html(driver, cutlist=True, maxchars=35000, text_only=text_only)
                result["content"] = html_content
            
            return ToolResult(success=True, data=result)
            
        except Exception as e:
            return ToolResult(success=False, error=str(e))


@dataclass
class WebExecuteJSTool(BaseTool):
    """执行 JavaScript 工具"""
    
    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="web_execute_js",
            description="Execute JavaScript in browser to control browser behavior",
            parameters={
                "type": "object",
                "properties": {
                    "script": {"type": "string", "description": "JavaScript code to execute"},
                    "switch_tab_id": {"type": "string", "description": "Tab ID to execute in"},
                    "save_to_file": {"type": "string", "description": "Save result to file"},
                    "no_monitor": {"type": "boolean", "description": "Disable page change monitor", "default": False},
                },
                "required": ["script"]
            },
            category=ToolCategory.WEB,
            tags=["browser", "javascript", "automation"],
        )
    
    async def execute(self, **kwargs) -> ToolResult:
        script = kwargs.get("script")
        
        if not script:
            return ToolResult(success=False, error="Script is required")
        
        try:
            try:
                from TMWebDriver import TMWebDriver
                import simphtml
                driver = TMWebDriver()
            except ImportError:
                return ToolResult(
                    success=False,
                    error="Browser driver not available"
                )
            
            switch_tab_id = kwargs.get("switch_tab_id")
            save_to_file = kwargs.get("save_to_file")
            no_monitor = kwargs.get("no_monitor", False)
            
            if switch_tab_id:
                driver.default_session_id = switch_tab_id
            
            result = simphtml.execute_js_rich(script, driver, no_monitor=no_monitor)
            
            if save_to_file and "js_return" in result:
                abs_path = os.path.abspath(save_to_file)
                with open(abs_path, 'w', encoding='utf-8') as f:
                    f.write(str(result["js_return"]))
                result["saved_to"] = abs_path
            
            return ToolResult(success=True, data=result)
            
        except Exception as e:
            return ToolResult(success=False, error=str(e))


@dataclass
class AskUserTool(BaseTool):
    """请求用户输入工具"""
    
    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="ask_user",
            description="Ask user for input with optional candidates",
            parameters={
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "Question to ask user"},
                    "candidates": {"type": "array", "items": {"type": "string"}, "description": "Optional candidate answers"},
                },
                "required": ["question"]
            },
            category=ToolCategory.USER,
            tags=["user", "input", "confirm"],
        )
    
    async def execute(self, **kwargs) -> ToolResult:
        question = kwargs.get("question", "请提供输入：")
        candidates = kwargs.get("candidates", [])
        
        return ToolResult(
            success=True,
            data={
                "status": "INTERRUPT",
                "intent": "HUMAN_INTERVENTION",
                "data": {
                    "question": question,
                    "candidates": candidates
                }
            }
        )


@dataclass
class UpdateWorkingCheckpointTool(BaseTool):
    """更新工作检查点工具"""
    
    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="update_working_checkpoint",
            description="Update key information for the current task",
            parameters={
                "type": "object",
                "properties": {
                    "key_info": {"type": "string", "description": "Key information to remember"},
                    "related_sop": {"type": "string", "description": "Related SOP file path"},
                },
            },
            category=ToolCategory.MEMORY,
            tags=["memory", "checkpoint", "context"],
        )
    
    async def execute(self, **kwargs) -> ToolResult:
        key_info = kwargs.get("key_info", "")
        related_sop = kwargs.get("related_sop", "")
        
        return ToolResult(
            success=True,
            data={
                "key_info": key_info,
                "related_sop": related_sop,
                "updated": True
            }
        )


@dataclass
class StartLongTermUpdateTool(BaseTool):
    """开始长期更新工具 - 触发经验结晶"""
    
    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="start_long_term_update",
            description="Trigger long-term memory update after task completion",
            parameters={
                "type": "object",
                "properties": {
                    "reflection": {"type": "string", "description": "Reflection on what was learned"},
                },
            },
            category=ToolCategory.MEMORY,
            tags=["memory", "crystallize", "learning"],
        )
    
    async def execute(self, **kwargs) -> ToolResult:
        reflection = kwargs.get("reflection", "")
        
        return ToolResult(
            success=True,
            data={
                "status": "LONG_TERM_UPDATE_STARTED",
                "reflection": reflection,
                "message": "Experience crystallization initiated"
            }
        )


import os
import importlib
