#!/usr/bin/env python3
"""GenericAgent CLI Frontend — Claude Code style terminal interaction.

Features:
  - Rich terminal UI with streaming Markdown rendering
  - Slash command autocomplete (press / to see commands)
  - /resume with interactive session picker
  - Multi-line input (Enter sends, Shift+Enter for newline)
  - Ctrl+C to abort current task

Usage:
  python -m frontends.cliapp
  python frontends/cliapp.py
"""

import os, sys, re, json, time, threading, queue

# ── Ensure project root on sys.path ──────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.history import InMemoryHistory
    from prompt_toolkit.formatted_text import FormattedText
    from prompt_toolkit.styles import Style as PTStyle
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.live import Live
    from rich.text import Text
    from rich.panel import Panel
    from rich.theme import Theme
except ImportError as e:
    missing = getattr(e, 'name', '') or str(e)
    print(f"缺少 CLI 依赖: {missing}\n请先运行: pip install rich prompt_toolkit", file=sys.stderr)
    raise SystemExit(2)

# ── Rich theme ───────────────────────────────────────────────────────
rich_theme = Theme({
    "agent": "cyan",
    "system": "yellow",
    "error": "bold red",
    "tool": "dim cyan",
    "info": "dim",
})
console = Console(theme=rich_theme)

# ── Slash command definitions ────────────────────────────────────────
SLASH_COMMANDS = [
    ("/help",      "显示帮助"),
    ("/status",    "查看状态"),
    ("/stop",      "停止当前任务"),
    ("/new",       "开启新对话并清空当前上下文"),
    ("/restore",   "恢复上次对话历史"),
    ("/resume",    "列出历史会话，交互选择恢复"),
    ("/resume N",  "直接恢复第 N 个会话"),
    ("/continue",  "同 /resume"),
    ("/model",     "查看当前模型列表"),
    ("/model NAME", "按编号或名称切换模型"),
    ("/llm",       "查看当前模型列表"),
    ("/llm N",     "切换到第 N 个模型"),
    ("/paste",     "粘贴剪贴板截图（或指定图片路径）"),
    ("/compact",   "压缩上下文"),
    ("/context",   "显示完整对话上下文（含 system prompt / working memory / 全部消息）"),
    ("/verbose",   "切换详细输出模式"),
]


class SlashCompleter(Completer):
    """Autocomplete for slash commands; /resume shows session list."""

    def __init__(self, get_sessions=None):
        self.get_sessions = get_sessions  # callable → [(name, summary), ...]

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith('/'):
            return

        # ── /resume special: show sessions ────────────────────────
        if text.startswith('/resume') and self.get_sessions:
            partial = text[len('/resume'):]
            try:
                sessions = self.get_sessions()
            except Exception:
                sessions = []
            for i, (name, summary) in enumerate(sessions[:15], 1):
                display = f"/resume {i}"
                desc = summary[:60] if summary else name
                if display.startswith(text):
                    yield Completion(
                        display,
                        start_position=-len(text),
                        display_meta=desc,
                    )
            if not sessions and text.strip() == '/resume':
                yield Completion(
                    '/resume',
                    start_position=-len(text),
                    display_meta='(无历史会话)',
                )
            return

        # ── Generic slash command completion ──────────────────────
        for cmd, desc in SLASH_COMMANDS:
            if cmd.startswith(text):
                yield Completion(
                    cmd,
                    start_position=-len(text),
                    display_meta=desc,
                )


class GenericAgentCLI:
    """Claude Code style CLI for GenericAgent."""

    def __init__(self, agent=None, llm_no=None, verbose=False):
        self.agent = agent
        self.llm_no = llm_no
        self.verbose = verbose
        self.running = True
        self._abort_event = threading.Event()
        self._pending_images = []  # list of data URI strings from /paste

        # prompt_toolkit session
        self.pt_session = PromptSession(
            history=InMemoryHistory(),
            completer=SlashCompleter(get_sessions=self._get_sessions),
            multiline=False,
            prompt_continuation="",
            style=PTStyle.from_dict({
                'prompt': 'bold green',
                'continuation': '#888888',
            }),
        )

    # ── Session list for /resume autocomplete ─────────────────────
    def _get_sessions(self):
        """Return [(name, summary), ...] from model_responses logs."""
        try:
            from frontends.continue_cmd import list_sessions
            sessions = list_sessions(exclude_pid=os.getpid())
            # list_sessions returns (path, mtime, first_user_text, n_rounds)
            return [(s[0], s[2] or '') for s in sessions[:15]]
        except Exception:
            # Fallback: scan model_responses dir directly
            log_dir = os.path.join(PROJECT_ROOT, 'temp', 'model_responses')
            if not os.path.isdir(log_dir):
                return []
            files = []
            for f in os.listdir(log_dir):
                if f.startswith('model_responses_') and f.endswith('.txt'):
                    path = os.path.join(log_dir, f)
                    files.append((path, os.path.getmtime(path)))
            files.sort(key=lambda x: -x[1])
            result = []
            for path, _ in files[:15]:
                try:
                    with open(path, encoding='utf-8', errors='replace') as fh:
                        content = fh.read(2000)
                    # Extract last user message as summary
                    users = re.findall(r'\[USER\]:\s*(.{1,80})', content)
                    summary = users[-1] if users else os.path.basename(path)
                    result.append((path, summary))
                except Exception:
                    result.append((path, os.path.basename(path)))
            return result

    # ── Initialize agent ──────────────────────────────────────────
    def _init_agent(self):
        if self.agent is not None:
            return
        from agentmain import GeneraticAgent
        self.agent = GeneraticAgent()
        if self.llm_no is not None:
            self.agent.next_llm(self.llm_no)
        self.agent.verbose = self.verbose
        self.agent.inc_out = True
        threading.Thread(target=self.agent.run, daemon=True).start()

    # ── Handle slash commands locally ─────────────────────────────
    def _handle_local_command(self, query):
        """Handle commands that don't need the agent. Returns (handled, response_text)."""
        q = query.strip()

        if q == '/help':
            lines = ["命令列表:\n"]
            for cmd, desc in SLASH_COMMANDS:
                lines.append(f"- `{cmd}`: {desc}")
            return True, '\n'.join(lines)

        if q == '/status':
            if self.agent is None:
                return True, "⏸️ Agent 未初始化"
            status = "🟢 运行中" if self.agent.is_running else "⚪ 空闲"
            llm = self.agent.get_llm_name() if hasattr(self.agent, 'get_llm_name') else '?'
            return True, f"{status}  |  模型: {llm}"

        if q == '/stop':
            if self.agent and self.agent.is_running:
                self.agent.abort()
                self._abort_event.set()
                return True, "⏹️ 已发送停止信号"
            return True, "⚪ 当前无运行中任务"

        if q == '/new':
            if self.agent:
                self.agent.history.clear()
                if self.agent.handler:
                    self.agent.handler = None
            return True, "🆕 已清空对话上下文"

        if q == '/verbose':
            if self.agent:
                self.agent.verbose = not self.agent.verbose
                return True, f"🔧 详细模式: {'开启' if self.agent.verbose else '关闭'}"
            return True, "🔧 Agent 未初始化"

        if q in ('/llm', '/model', '/models'):
            if self.agent is None:
                self._init_agent()
            lines = ["模型列表:\n"]
            for idx, name, active in self.agent.list_llms():
                marker = " ◀ 当前" if active else ""
                lines.append(f"- `{idx}`: {name}{marker}")
            lines.append("\n用法: `/model 2` 或 `/model opencode-go-dsv4f`")
            return True, '\n'.join(lines)

        if q.startswith('/llm ') or q.startswith('/model '):
            if self.agent is None:
                self._init_agent()
            cmd, _, selector = q.partition(' ')
            selector = selector.strip()
            try:
                if hasattr(self.agent, 'select_llm'):
                    idx = self.agent.select_llm(selector)
                else:
                    idx = int(selector)
                    self.agent.next_llm(idx)
                return True, f"✅ 已切换到模型 {idx}: {self.agent.get_llm_name()}"
            except (ValueError, IndexError):
                return True, f"❌ 用法: {cmd} <编号或名称>"
            except Exception as e:
                return True, f"❌ 切换失败: {e}"

        if q == '/paste' or q.startswith('/paste '):
            return self._handle_paste(q)

        if q == '/context':
            if self.agent is None:
                return True, "⏸️ Agent 未初始化"
            lines = []
            try:
                backend = self.agent.llmclient.backend
                lines.append("━" * 50)
                lines.append("📋 完整对话上下文")
                lines.append("━" * 50)

                # 1. System Prompt
                system = getattr(backend, 'system', '') or ''
                if system:
                    lines.append(f"\n🔧 SYSTEM PROMPT ({len(system)} chars):")
                    lines.append("-" * 40)
                    lines.append(system)
                    lines.append("-" * 40)

                # 2. Working Memory (from handler)
                if self.agent.handler:
                    h = self.agent.handler
                    lines.append("\n📝 WORKING MEMORY:")
                    if h.working.get('key_info'):
                        lines.append(f"  key_info: {h.working['key_info']}")
                    if h.working.get('related_sop'):
                        lines.append(f"  related_sop: {h.working['related_sop']}")
                    if h.working.get('passed_sessions'):
                        lines.append(f"  passed_sessions: {h.working['passed_sessions']}")
                    if h.history_info:
                        lines.append(f"  history_info (last 5):")
                        for entry in h.history_info[-5:]:
                            lines.append(f"    {entry[:200]}")

                # 3. History (user/agent log)
                if self.agent.history:
                    lines.append(f"\n💬 USER/AGENT LOG ({len(self.agent.history)} entries):")
                    lines.append("-" * 40)
                    for entry in self.agent.history:
                        tag = entry[:6] if entry.startswith('[USER]') or entry.startswith('[Agent') else '  '
                        text = entry[7:] if entry.startswith('[USER]') or entry.startswith('[Agent]') else entry
                        lines.append(f"  {tag} {text[:300]}")

                # 4. Backend session history (full LLM messages, no truncation)
                hist = getattr(backend, 'history', [])
                if hist:
                    lines.append(f"\n🤖 BACKEND MESSAGE HISTORY ({len(hist)} messages):")
                    lines.append("-" * 40)
                    for i, msg in enumerate(hist):
                        role = msg.get('role', '?')
                        content = msg.get('content', '')
                        if isinstance(content, list):
                            texts = []
                            for part in content:
                                if isinstance(part, dict):
                                    if part.get('type') in ('text', 'input_text', 'output_text'):
                                        texts.append(part.get('text', ''))
                                    elif part.get('type') == 'tool_use':
                                        texts.append(f"[tool_use: {part.get('name', '?')}]")
                                    elif part.get('type') == 'tool_result':
                                        texts.append(f"[tool_result]")
                                    else:
                                        texts.append(f"[{part.get('type', '?')}]")
                            content = ' '.join(texts)
                        if not isinstance(content, str):
                            content = str(content)
                        lines.append(f"\n  [{i}] {role}:")
                        # Don't truncate — show full content
                        lines.append(f"    {content}")
                else:
                    lines.append("\n🤖 BACKEND MESSAGE HISTORY: (empty)")

                lines.append("\n" + "━" * 50)
            except Exception as e:
                lines.append(f"\n❌ 获取上下文出错: {e}")
            return True, '\n'.join(lines)

        if q == '/compact':
            if self.agent and self.agent.history:
                # Keep only last few exchanges for compacting
                old_len = len(self.agent.history)
                self.agent.history = self.agent.history[-4:]
                return True, f"📦 上下文已压缩: {old_len} → {len(self.agent.history)} 条"
            return True, "📦 无上下文可压缩"

        if q == '/restore':
            if self.agent is None:
                self._init_agent()
            try:
                from frontends.continue_cmd import restore, reset_conversation
                reset_conversation(self.agent, message=None)
                msg, _ = restore(self.agent)
                return True, f"✅ {msg}"
            except Exception as e:
                return True, f"❌ 恢复失败: {e}"

        if q == '/resume':
            return self._handle_resume('')

        if q.startswith('/resume '):
            return self._handle_resume(q[len('/resume '):])

        if q == '/continue':
            return self._handle_resume('')

        if q.startswith('/continue '):
            return self._handle_resume(q[len('/continue '):])

        return False, None

    def _handle_resume(self, arg):
        """Interactive session resume with inline number selection and conversation preview."""
        try:
            from frontends.continue_cmd import list_sessions, restore, reset_conversation, extract_ui_messages
        except ImportError:
            from continue_cmd import list_sessions, restore, reset_conversation, extract_ui_messages

        sessions = list_sessions(exclude_pid=os.getpid())
        if not sessions:
            return True, "📭 没有可恢复的历史会话"

        if arg.strip():
            # Direct index selection
            try:
                idx = int(arg.strip()) - 1
                if 0 <= idx < len(sessions):
                    self._init_agent()
                    reset_conversation(self.agent, message=None)
                    msg, _ = restore(self.agent, sessions[idx][0])
                    preview = self._build_resume_preview(sessions[idx][0])
                    return True, f"{msg}\n{preview}"
                else:
                    return True, f"❌ 索引越界（有效范围 1-{len(sessions)}）"
            except ValueError:
                pass

        # Show list
        lines = ["📋 历史会话:\n"]
        for i, sess in enumerate(sessions[:15], 1):
            path, mtime, first_user, n_rounds = sess[0], sess[1], sess[2], sess[3]
            name = os.path.basename(path)
            summary = first_user or ''
            mtime_str = time.strftime('%m-%d %H:%M', time.localtime(mtime))
            lines.append(f"  {i:2d}. [{mtime_str}] {n_rounds}轮 · {summary or name}")

        console.print()
        console.print(Markdown('\n'.join(lines)))
        console.print()

        # Interactive selection: let user type a number directly
        try:
            choice = self.pt_session.prompt(
                FormattedText([('class:prompt', '选择会话编号（直接输入数字，回车取消）: ')]),
            )
        except (KeyboardInterrupt, EOFError):
            return True, ""
        choice = choice.strip()
        if not choice:
            return True, ""
        try:
            idx = int(choice) - 1
        except ValueError:
            return True, "❌ 请输入数字"
        if not (0 <= idx < len(sessions)):
            return True, f"❌ 索引越界（有效范围 1-{len(sessions)}）"

        self._init_agent()
        reset_conversation(self.agent, message=None)
        msg, _ = restore(self.agent, sessions[idx][0])
        preview = self._build_resume_preview(sessions[idx][0])
        return True, f"{msg}\n{preview}"

    def _build_resume_preview(self, path):
        """Show full raw conversation content from the log file."""
        try:
            with open(path, encoding='utf-8', errors='replace') as f:
                raw = f.read()
        except Exception as e:
            return f"\n⚠️ 无法读取日志: {e}"

        from frontends.continue_cmd import _pairs, _user_text, _assistant_text
        pairs = _pairs(raw)
        if not pairs:
            return "\n⚠️ 未找到对话内容"

        lines = ["\n📜 **历史会话原始内容**:\n"]
        for i, (prompt, response) in enumerate(pairs, 1):
            user = _user_text(prompt)
            if user:
                lines.append(f"━━━ 第 {i} 轮 ━━━")
                lines.append(f"🧑 {user}")
            asst = _assistant_text(response)
            if asst:
                lines.append(f"")
                lines.append(asst)
        return '\n'.join(lines)

    # ── Paste image from clipboard or file ──────────────────────
    def _handle_paste(self, query):
        """Handle /paste command: read image from clipboard or file path, store as pending."""
        from datetime import datetime

        arg = query.strip()[6:].strip()  # strip "/paste"
        image_data = None
        source_desc = ""

        if arg:
            # /paste /path/to/image.png
            path = os.path.expanduser(arg)
            if not os.path.isfile(path):
                return True, f"❌ 文件不存在: {path}"
            ext = os.path.splitext(path)[1].lower()
            mime_map = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                        '.gif': 'image/gif', '.webp': 'image/webp', '.bmp': 'image/bmp'}
            mime = mime_map.get(ext)
            if not mime:
                return True, f"❌ 不支持的图片格式: {ext}（支持: png/jpg/gif/webp/bmp）"
            with open(path, 'rb') as f:
                image_data = f.read()
            source_desc = os.path.basename(path)
        else:
            # /paste — read from clipboard (macOS AppKit)
            try:
                from AppKit import NSPasteboard, NSData
                pb = NSPasteboard.generalPasteboard()
                # Try TIFF first (most common clipboard image format on macOS)
                image_types = ['public.tiff', 'public.png', 'public.jpeg', 'public.gif']
                raw = None
                for utype in image_types:
                    data = pb.dataForType_(utype)
                    if data and data.length() > 0:
                        raw = bytes(data)
                        if utype == 'public.png':
                            mime = 'image/png'
                        elif utype == 'public.jpeg':
                            mime = 'image/jpeg'
                        elif utype == 'public.gif':
                            mime = 'image/gif'
                        else:
                            mime = 'image/png'  # TIFF → convert to PNG below
                        break
                if raw is None:
                    return True, "❌ 剪贴板中没有图片。用法: /paste [图片路径]"
                image_data = raw
                source_desc = "剪贴板截图"
            except ImportError:
                return True, "❌ 剪贴板读取需要 macOS (AppKit)。用法: /paste /path/to/image.png"

        if not image_data:
            return True, "❌ 未能获取图片数据"

        # Save to temp/uploaded/
        upload_dir = os.path.join(PROJECT_ROOT, 'temp', 'uploaded')
        os.makedirs(upload_dir, exist_ok=True)
        ts = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        ext_map = {'image/png': '.png', 'image/jpeg': '.jpg', 'image/gif': '.gif', 'image/webp': '.webp'}
        ext = ext_map.get(mime, '.png')
        saved_path = os.path.join(upload_dir, f"{ts}{ext}")
        with open(saved_path, 'wb') as f:
            f.write(image_data)

        # Store saved file path as pending (agent will read via file_read)
        self._pending_images.append(saved_path)

        size_kb = len(image_data) / 1024
        n = len(self._pending_images)
        return True, f"🖼️ 已粘贴图片: {source_desc} ({size_kb:.0f}KB)\n📎 待发送图片: {n} 张（输入问题后一起发送）"

    # ── Stream response from display_queue ────────────────────────
    def _stream_response(self, display_queue):
        """Consume display_queue and render streaming Markdown via Rich."""
        buffer = ""
        spinner_chars = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        spin_idx = 0

        try:
            with Live(console=console, refresh_per_second=8, vertical_overflow='visible') as live:
                while True:
                    try:
                        item = display_queue.get(timeout=0.1)
                    except queue.Empty:
                        # Show spinner while waiting
                        if not buffer:
                            spin_idx = (spin_idx + 1) % len(spinner_chars)
                            live.update(Text(f" {spinner_chars[spin_idx]} 思考中...", style="dim"))
                        continue

                    if 'next' in item:
                        chunk = item['next']
                        buffer += chunk
                        # Render accumulated text as Markdown
                        try:
                            live.update(Markdown(self._clean_for_display(buffer)))
                        except Exception:
                            live.update(Text(buffer))

                    elif 'done' in item:
                        final = item['done']
                        # Final render with full Markdown
                        try:
                            cleaned = self._clean_for_display(final)
                            live.update(Markdown(cleaned))
                        except Exception:
                            live.update(Text(final))
                        break

        except KeyboardInterrupt:
            if self.agent:
                self.agent.abort()
            console.print("\n[interrupted]", style="error")

    def _clean_for_display(self, text):
        """Clean raw LLM output for terminal display."""
        if not text:
            return ""

        if re.match(r'\s*(?:!!!)?Error:', text):
            return f"```\n{text.strip()}\n```"

        # Remove internal tags that are not useful in CLI, while showing called tools briefly.
        tool_uses = []
        for body in re.findall(r'<tool_use>(.*?)</tool_use>', text, flags=re.DOTALL):
            name = None
            xml_match = re.search(r'<name>(.*?)</name>', body, flags=re.DOTALL)
            if xml_match:
                name = xml_match.group(1).strip()
            else:
                try:
                    data = json.loads(body.strip())
                    name = data.get('name') or data.get('tool') or data.get('function')
                except Exception:
                    pass
            if name:
                tool_uses.append(name)
        text = re.sub(r'<tool_use>.*?</tool_use>', '', text, flags=re.DOTALL)
        if tool_uses:
            tool_summary = ' '.join(f'`{t}`' for t in tool_uses)
            # Prepend tool summary if not already in text
            if tool_summary not in text:
                text = f"Tools: {tool_summary}\n\n{text}"
        # Clean up excessive newlines
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()

    # ── Submit task to agent ──────────────────────────────────────
    def _submit_task(self, query):
        """Submit query to agent and stream response."""
        self._abort_event.clear()

        # Let agent handle slash commands we don't handle locally
        handled, response = self._handle_local_command(query)
        if handled:
            if response:
                console.print()
                console.print(Markdown(response))
                console.print()
            return

        # Attach pending images to query (file paths only, agent reads via file_read)
        if self._pending_images:
            image_parts = []
            for i, img_path in enumerate(self._pending_images, 1):
                image_parts.append(f"\n[图片 {i}] 文件路径: {img_path}")
            query = query + "\n" + "".join(image_parts) + "\n请用 file_read 读取以上图片文件。"
            n = len(self._pending_images)
            self._pending_images = []
            console.print(f"📎 已附带 {n} 张图片", style="dim")

        # Forward to agent
        self._init_agent()
        dq = self.agent.put_task(query, source='user')
        self._stream_response(dq)
        console.print()

    # ── Main loop ─────────────────────────────────────────────────
    def run(self):
        """Main REPL loop."""
        console.print(Panel(
            "[bold cyan]GenericAgent CLI[/bold cyan] — Claude Code 风格终端交互\n"
            "输入 [dim]/help[/dim] 查看命令  |  [dim]Ctrl+C[/dim] 中断任务  |  [dim]Ctrl+D[/dim] 退出",
            border_style="cyan",
            padding=(0, 2),
        ))

        while self.running:
            try:
                # Get user input with prompt_toolkit
                query = self.pt_session.prompt(
                    FormattedText([('class:prompt', '❯ ')]),
                )
                if query is None:
                    break
                query = query.strip()
                if not query:
                    continue

                self._submit_task(query)

            except KeyboardInterrupt:
                # If agent is running, abort it; otherwise exit
                if self.agent and self.agent.is_running:
                    self.agent.abort()
                    console.print("\n⏹️ 任务已中断", style="system")
                else:
                    console.print("\n👋 再见!")
                    break
            except EOFError:
                console.print("\n👋 再见!")
                break

        # Cleanup
        if self.agent and hasattr(self.agent, 'handler') and self.agent.handler:
            self.agent.handler.code_stop_signal.append(1)


def main():
    import argparse
    parser = argparse.ArgumentParser(description='GenericAgent CLI - Claude Code style')
    parser.add_argument('--llm_no', type=int, default=None, help='LLM model index')
    parser.add_argument('--verbose', action='store_true', help='Verbose output')
    args = parser.parse_args()

    cli = GenericAgentCLI(llm_no=args.llm_no, verbose=args.verbose)
    cli.run()


if __name__ == '__main__':
    main()
