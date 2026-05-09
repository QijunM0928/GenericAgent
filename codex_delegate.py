#!/usr/bin/env python3
"""
Codex Delegate - Claude Code 通过 codex exec 非交互式调用 Codex
用法:
  python3 codex_delegate.py "你的任务描述" [--cwd /path/to/project] [--timeout 300] [--auto-approve]
  
作为模块导入:
  from codex_delegate import call_codex
  result = call_codex("实现 JWT 认证", cwd="/path/to/project")
"""

import subprocess, json, sys, os, tempfile, time
from pathlib import Path

CODEX = "/Applications/Codex.app/Contents/Resources/codex"

def call_codex(prompt: str, cwd: str = None, timeout: int = 300, 
               full_auto: bool = True, model: str = None) -> dict:
    """
    调用 Codex exec 执行任务，返回结构化结果
    
    Args:
        prompt: 任务描述
        cwd: 工作目录（必须是 git 仓库）
        timeout: 超时秒数
        full_auto: 是否全自动模式（不需要人工确认）
        model: 指定模型
    
    Returns:
        {
            "success": bool,
            "final_message": str,      # Codex 最终回复
            "commands_run": [...],      # 执行的命令列表
            "files_modified": [...],    # 修改的文件
            "thread_id": str,           # 会话 ID
            "usage": {...},             # token 用量
            "raw_events": [...]         # 原始事件流
        }
    """
    cwd = cwd or os.getcwd()
    output_file = tempfile.mktemp(suffix=".txt", prefix="codex_out_")
    
    cmd = [CODEX, "exec"]
    if full_auto:
        cmd.append("--full-auto")
    cmd.extend(["--json", "-o", output_file])
    if model:
        cmd.extend(["-c", f"model={model}"])
    cmd.append(prompt)
    
    try:
        result = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"Timeout after {timeout}s", "final_message": ""}
    
    # 解析 JSONL 事件流
    events = []
    commands_run = []
    files_modified = []
    final_message = ""
    thread_id = ""
    usage = {}
    
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
            events.append(event)
            
            if event.get("type") == "thread.started":
                thread_id = event.get("thread_id", "")
            
            elif event.get("type") == "item.completed":
                item = event.get("item", {})
                if item.get("type") == "command_execution":
                    commands_run.append({
                        "command": item.get("command", ""),
                        "exit_code": item.get("exit_code"),
                        "output": item.get("aggregated_output", "")[:500]
                    })
                elif item.get("type") == "agent_message":
                    final_message = item.get("text", "")
            
            elif event.get("type") == "turn.completed":
                usage = event.get("usage", {})
        except json.JSONDecodeError:
            pass
    
    # 读取 -o 输出文件
    if os.path.exists(output_file):
        with open(output_file) as f:
            file_output = f.read().strip()
        if file_output and not final_message:
            final_message = file_output
        os.unlink(output_file)
    
    return {
        "success": result.returncode == 0,
        "final_message": final_message,
        "commands_run": commands_run,
        "files_modified": files_modified,
        "thread_id": thread_id,
        "usage": usage,
        "raw_events": events
    }


def call_codex_with_verification(prompt: str, cwd: str = None, 
                                  verify_cmd: str = None,
                                  max_retries: int = 2,
                                  timeout: int = 300) -> dict:
    """
    调用 Codex 并自动验收，不合格则自动返工
    
    Args:
        prompt: 任务描述
        cwd: 工作目录
        verify_cmd: 验收命令（如 "pytest tests/ -v"）
        max_retries: 最大返工次数
        timeout: 单次超时
    
    Returns:
        同 call_codex，额外包含 verification 字段
    """
    cwd = cwd or os.getcwd()
    current_prompt = prompt
    
    for attempt in range(max_retries + 1):
        result = call_codex(current_prompt, cwd=cwd, timeout=timeout)
        
        if not result["success"]:
            current_prompt = f"上次执行失败。错误信息：{result.get('error', 'unknown')}。请重试：{prompt}"
            continue
        
        # 运行验收命令
        if verify_cmd:
            verify_result = subprocess.run(
                verify_cmd, shell=True, cwd=cwd, 
                capture_output=True, text=True, timeout=120
            )
            result["verification"] = {
                "command": verify_cmd,
                "passed": verify_result.returncode == 0,
                "output": verify_result.stdout[-500:] if verify_result.stdout else "",
                "errors": verify_result.stderr[-500:] if verify_result.stderr else ""
            }
            
            if verify_result.returncode == 0:
                return result
            
            # 验收失败，自动返工
            current_prompt = (
                f"上次修改未通过验收。\n"
                f"验收命令: {verify_cmd}\n"
                f"错误输出: {verify_result.stderr[-1000:]}\n"
                f"请修正问题。原始任务：{prompt}"
            )
        else:
            return result
    
    return result


# CLI 入口
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Codex Delegate - 通过 CLI 调用 Codex")
    parser.add_argument("prompt", help="任务描述")
    parser.add_argument("--cwd", help="工作目录", default=None)
    parser.add_argument("--timeout", type=int, default=300, help="超时秒数")
    parser.add_argument("--verify", help="验收命令", default=None)
    parser.add_argument("--max-retries", type=int, default=2, help="最大返工次数")
    parser.add_argument("--model", help="指定模型", default=None)
    
    args = parser.parse_args()
    
    if args.verify:
        result = call_codex_with_verification(
            args.prompt, cwd=args.cwd, verify_cmd=args.verify,
            max_retries=args.max_retries, timeout=args.timeout
        )
    else:
        result = call_codex(args.prompt, cwd=args.cwd, timeout=args.timeout, model=args.model)
    
    # 输出结果
    print(json.dumps(result, indent=2, ensure_ascii=False))
