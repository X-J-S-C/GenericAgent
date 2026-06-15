import os, sys, threading, queue, time, json, re, random, locale
os.environ.setdefault('GA_LANG', 'zh' if any(k in (locale.getlocale()[0] or '').lower() for k in ('zh', 'chinese')) else 'en')
if sys.stdout is None: sys.stdout = open(os.devnull, "w")
elif hasattr(sys.stdout, 'reconfigure'): sys.stdout.reconfigure(errors='replace')
if sys.stderr is None: sys.stderr = open(os.devnull, "w")
elif hasattr(sys.stderr, 'reconfigure'): sys.stderr.reconfigure(errors='replace')
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from llmcore import reload_mykeys, LLMSession, ToolClient, ClaudeSession, MixinSession, NativeToolClient, NativeClaudeSession, NativeOAISession
from agent_loop import agent_runner_loop
from ga import GenericAgentHandler, smart_format, get_global_memory, format_error, consume_file

script_dir = os.path.dirname(os.path.abspath(__file__))
def load_tool_schema(suffix=''):
    global TOOLS_SCHEMA
    TS = open(os.path.join(script_dir, f'assets/tools_schema{suffix}.json'), 'r', encoding='utf-8').read()
    TOOLS_SCHEMA = json.loads(TS if os.name == 'nt' else TS.replace('powershell', 'bash'))
load_tool_schema()

lang_suffix = '_en' if os.environ.get('GA_LANG', '') == 'en' else ''
mem_dir = os.path.join(script_dir, 'memory')
if not os.path.exists(mem_dir): os.makedirs(mem_dir)
mem_txt = os.path.join(mem_dir, 'global_mem.txt')
if not os.path.exists(mem_txt): open(mem_txt, 'w', encoding='utf-8').write('# [Global Memory - L2]\n')
mem_insight = os.path.join(mem_dir, 'global_mem_insight.txt')
if not os.path.exists(mem_insight):
    t = os.path.join(script_dir, f'assets/global_mem_insight_template{lang_suffix}.txt')
    open(mem_insight, 'w', encoding='utf-8').write(open(t, encoding='utf-8').read() if os.path.exists(t) else '')
cdp_cfg = os.path.join(script_dir, 'assets/tmwd_cdp_bridge/config.js')
if not os.path.exists(cdp_cfg):
    try:
        os.makedirs(os.path.dirname(cdp_cfg), exist_ok=True)
        open(cdp_cfg, 'w', encoding='utf-8').write(f"const TID = '__ljq_{hex(random.randint(0, 99999999))[2:8]}';")
    except Exception as e: print(f'[WARN] CDP config init failed: {e} — advanced web features (tmwebdriver) will be unavailable.')

def get_system_prompt():
    with open(os.path.join(script_dir, f'assets/sys_prompt{lang_suffix}.txt'), 'r', encoding='utf-8') as f: prompt = f.read()
    prompt += f"\nToday: {time.strftime('%Y-%m-%d %a')}\n"
    prompt += get_global_memory()
    return prompt

# ============================================================
# 会话隔离（Session Isolation）- 任务 05
# ============================================================
_DEFAULT_SESSION_ID = 'default'
_MAX_CONCURRENT_SESSIONS = 64          # 最大并发会话数（软限制）
_SESSION_IDLE_SECS = 12 * 3600         # 会话空闲超时：12 小时后自动回收
_SESSION_TOTAL_SECS = 3 * 24 * 3600    # 会话最大总存活时间：3 天

_SESSION_REGISTRY_LOCK = threading.RLock()
_SESSION_REGISTRY = {}   # session_id -> SessionState
_SESSION_MRU = []        # 最近使用顺序（LRU 式清理）


class SessionState:
    """单个会话的独立状态容器，不与其他会话共享任何内部状态。"""

    __slots__ = (
        'session_id', 'created_at', 'last_used_at',
        'llm_no', 'llmclients', 'llmclient',
        'history', 'handler', 'task_queue',
        'verbose', 'running', 'stop_sig',
        'worker_thread', 'title',
    )

    def __init__(self, session_id):
        self.session_id = session_id
        now = time.time()
        self.created_at = now
        self.last_used_at = now
        self.llm_no = 0
        self.llmclients = []
        self.llmclient = None
        self.history = []
        self.handler = None
        self.task_queue = queue.Queue()
        self.verbose = True
        self.running = False
        self.stop_sig = False
        self.worker_thread = None
        self.title = None
        self.load_llm_sessions()

    def touch(self):
        self.last_used_at = time.time()
        with _SESSION_REGISTRY_LOCK:
            if self.session_id in _SESSION_MRU:
                _SESSION_MRU.remove(self.session_id)
            _SESSION_MRU.append(self.session_id)

    def load_llm_sessions(self):
        mykeys, _changed = reload_mykeys()
        llm_sessions = []
        for _k, cfg in mykeys.items():
            if not any(x in _k for x in ['api', 'config', 'cookie']): continue
            try:
                if 'native' in _k and 'claude' in _k:
                    llm_sessions.append(NativeToolClient(NativeClaudeSession(cfg=cfg)))
                elif 'native' in _k and 'oai' in _k:
                    llm_sessions.append(NativeToolClient(NativeOAISession(cfg=cfg)))
                elif 'claude' in _k:
                    llm_sessions.append(ToolClient(ClaudeSession(cfg=cfg)))
                elif 'oai' in _k:
                    llm_sessions.append(ToolClient(LLMSession(cfg=cfg)))
                elif 'mixin' in _k:
                    llm_sessions.append({'mixin_cfg': cfg})
                else:
                    # 其他含 api/config/cookie 的 key 一律当作 OpenAI 兼容会话
                    llm_sessions.append(ToolClient(LLMSession(cfg=cfg)))
            except Exception:
                pass
        for i, s in enumerate(llm_sessions):
            if isinstance(s, dict) and 'mixin_cfg' in s:
                try:
                    mixin = MixinSession(llm_sessions, s['mixin_cfg'])
                    if isinstance(mixin._sessions[0], (NativeClaudeSession, NativeOAISession)): llm_sessions[i] = NativeToolClient(mixin)
                    else: llm_sessions[i] = ToolClient(mixin)
                except Exception as e: print(f'[WARN] Failed to init MixinSession with cfg {s["mixin_cfg"]}: {e}')
        self.llmclients = llm_sessions
        if not self.llmclients:
            raise RuntimeError('[ERROR] 没有可用的 LLM 配置，请检查 mykey.py / mykey.json')
        self.llmclient = self.llmclients[self.llm_no % len(self.llmclients)]

    def next_llm(self, n=-1):
        self.load_llm_sessions()
        self.llm_no = ((self.llm_no + 1) if n < 0 else n) % len(self.llmclients)
        self.llmclient = self.llmclients[self.llm_no]
        # 注意：不同会话之间不再传递 history；每个 Session 有独立的 backend.history
        self.llmclient.last_tools = ''
        name = self.get_llm_name(model=True)
        if 'glm' in name or 'minimax' in name or 'kimi' in name: load_tool_schema('_cn')
        else: load_tool_schema()
        return name

    def list_llms(self):
        self.load_llm_sessions()
        return [(i, self.get_llm_name(b), i == self.llm_no) for i, b in enumerate(self.llmclients)]

    def get_llm_name(self, b=None, model=False):
        b = self.llmclient if b is None else b
        if isinstance(b, dict): return 'BADCONFIG_MIXIN'
        if model: return b.backend.model.lower()
        return f"{type(b.backend).__name__}/{b.backend.name}"

    # ---- 元数据（用于前端展示） ----
    def set_title(self, title):
        self.title = (title or '').strip()[:80] or None

    def to_dict(self):
        return {
            'session_id': self.session_id,
            'title': self.title or _title_from_history(self.history),
            'turns': len(self.history),
            'created_at': int(self.created_at),
            'last_used_at': int(self.last_used_at),
            'llm': self.get_llm_name(model=True),
            'running': bool(self.running),
        }


def _title_from_history(history):
    if not history: return '(新会话)'
    for h in reversed(history[:5]):
        h = str(h)
        if h.startswith('[USER]'):
            body = h[len('[USER]'):].strip()
            return body[:40] if body else '(新会话)'
    return '(新会话)'


def _gc_expired_sessions():
    """在 _SESSION_REGISTRY_LOCK 外调用：清理已超时 / 超出最大并发数的空闲会话。"""
    with _SESSION_REGISTRY_LOCK:
        now = time.time()
        # 1) 按空闲超时 + 总存活时间清理
        expired = []
        for sid, s in list(_SESSION_REGISTRY.items()):
            if s.running: continue  # 正在运行的会话保留
            if (now - s.last_used_at) > _SESSION_IDLE_SECS: expired.append(sid)
            elif (now - s.created_at) > _SESSION_TOTAL_SECS: expired.append(sid)
        for sid in expired:
            _SESSION_REGISTRY.pop(sid, None)
            if sid in _SESSION_MRU: _SESSION_MRU.remove(sid)
        # 2) 若总数量仍超过上限，按 LRU 清理未在运行的会话
        while len(_SESSION_REGISTRY) > _MAX_CONCURRENT_SESSIONS:
            victim = None
            for sid in _SESSION_MRU:
                if not _SESSION_REGISTRY[sid].running:
                    victim = sid; break
            if victim is None: break
            _SESSION_REGISTRY.pop(victim, None)
            _SESSION_MRU.remove(victim)


def get_session(session_id=None, create=True):
    """获取（或创建）一个会话。`session_id=None` 时使用默认会话。"""
    sid = (session_id or _DEFAULT_SESSION_ID).strip() or _DEFAULT_SESSION_ID
    with _SESSION_REGISTRY_LOCK:
        s = _SESSION_REGISTRY.get(sid)
        if s is None and create:
            _gc_expired_sessions()
            # 超限时不允许新建，改为复用最旧空闲
            if len(_SESSION_REGISTRY) >= _MAX_CONCURRENT_SESSIONS:
                for candidate in _SESSION_MRU:
                    if not _SESSION_REGISTRY[candidate].running:
                        sid = candidate; s = _SESSION_REGISTRY[sid]
                        # 重置旧会话内容
                        s.__init__(sid); break
                if s is None:
                    raise RuntimeError(f'[ERROR] 并发会话数已达上限 ({_MAX_CONCURRENT_SESSIONS})，请稍后重试。')
            else:
                s = SessionState(sid)
                _SESSION_REGISTRY[sid] = s
        if s is not None:
            s.touch()
        return s


def list_sessions():
    """返回所有会话的摘要列表（按最近使用排序）。"""
    _gc_expired_sessions()
    with _SESSION_REGISTRY_LOCK:
        return [_SESSION_REGISTRY[sid].to_dict() for sid in _SESSION_MRU if sid in _SESSION_REGISTRY]


def delete_session(session_id):
    """删除指定会话。若会话正在运行，将先发送 stop 信号。"""
    sid = (session_id or '').strip()
    if not sid or sid == _DEFAULT_SESSION_ID:
        return False, '默认会话不能删除。'
    with _SESSION_REGISTRY_LOCK:
        s = _SESSION_REGISTRY.get(sid)
        if s is None: return False, f'会话 {sid} 不存在。'
        if s.running:
            s.stop_sig = True
            if s.handler is not None:
                try: s.handler.code_stop_signal.append(1)
                except: pass
        _SESSION_REGISTRY.pop(sid, None)
        if sid in _SESSION_MRU: _SESSION_MRU.remove(sid)
        return True, f'会话 {sid} 已删除。'


# ============================================================
# 会话级 Agent 引擎
# ============================================================
class GeneraticAgent:
    """
    会话级 Agent：不再持有任何全局可变状态。
    所有运行时状态保存在传入的 `SessionState` 对象中。
    """

    def __init__(self, session: SessionState):
        self.session = session

    def abort(self):
        self.session.stop_sig = True
        if self.session.handler is not None:
            try: self.session.handler.code_stop_signal.append(1)
            except: pass

    def put_task(self, query, source="user", images=None):
        display_queue = queue.Queue()
        self.session.task_queue.put({"query": query, "source": source, "images": images or [], "output": display_queue})
        return display_queue

    def _handle_slash_cmd(self, raw_query, display_queue):
        """
        会话内命令（以 / 开头）：
          /session.new [名称]          切换到一个全新会话（新 session_id 自动生成）
          /session.list                列出当前所有会话
          /session.switch <id>         切换到指定会话
          /session.clear               清空当前会话的历史 / 记忆
          /session.delete <id>         删除指定会话
          /session.rename <标题>       为当前会话命名
          /session.llm [idx]           切换当前会话使用的 LLM 配置
          /llm.next                    同上（简写）
          /session.key=...             （兼容旧协议）修改当前会话 backend 属性
        返回: (new_session_id or None, handled_flag, text)
          - handled=True 表示命令已被就地处理（run 循环不应继续）
          - new_session_id 不为 None 时表示要切换到另一个会话继续处理
        """
        if not raw_query.startswith('/'):
            return None, False, raw_query

        q = raw_query.strip()
        lower = q.lower()

        # /session.new [name]
        if lower.startswith('/session.new') or lower.startswith('/session new'):
            name = raw_query[len('/session.new'):].strip()
            new_sid = _gen_session_id(name)
            try:
                ns = get_session(new_sid, create=True)
                if name: ns.set_title(name)
            except Exception as e:
                display_queue.put({'done': smart_format(f'❌ {e}', max_str_len=500), 'source': 'system'})
                return None, True, None
            display_queue.put({'done': smart_format(f'✅ 已创建并切换到新会话 session_id=`{new_sid}`', max_str_len=500),
                               'source': 'system', 'new_session_id': new_sid})
            return new_sid, True, None

        # /session.list
        if lower in ('/session.list', '/session list', '/ls', '/sessions'):
            slist = list_sessions()
            txt_lines = [f"📋 当前会话共 {len(slist)} 个（按最近使用排序）：\n"]
            cur = self.session.session_id
            for i, s in enumerate(slist):
                flag = '👉' if s['session_id'] == cur else '  '
                stat = '🟢运行中' if s['running'] else '⚪空闲'
                age_h = int((time.time() - s['last_used_at']) / 60)
                age = f"{age_h}min前活跃" if age_h < 60 else f"{int(age_h/60)}h前活跃"
                txt_lines.append(f"{flag} `{s['session_id']}` 「{s['title']}」 — {stat}, turns={s['turns']}, LLM={s['llm']}, {age}")
            display_queue.put({'done': '\n'.join(txt_lines), 'source': 'system'})
            return None, True, None

        # /session.switch <id>
        m = re.match(r'^/session\.(switch|use)\s+(.+)$', q, re.IGNORECASE) or re.match(r'^/session\s+(switch|use)\s+(.+)$', q, re.IGNORECASE)
        if m:
            target = m.group(2).strip()
            try:
                ns = get_session(target, create=False)
            except Exception as e:
                display_queue.put({'done': smart_format(f'❌ {e}', max_str_len=500), 'source': 'system'})
                return None, True, None
            if ns is None:
                display_queue.put({'done': smart_format(f'❌ 会话 `{target}` 不存在，无法切换。', max_str_len=500), 'source': 'system'})
                return None, True, None
            display_queue.put({'done': smart_format(f'✅ 已切换到会话 session_id=`{target}`', max_str_len=500),
                               'source': 'system', 'new_session_id': target})
            return target, True, None

        # /session.clear
        if lower in ('/session.clear', '/session clear', '/clear'):
            self.session.history = []
            self.session.handler = None
            # 清空每个 backend 的内部 history（真正隔离的关键）
            for c in self.session.llmclients:
                try:
                    if hasattr(c, 'backend') and hasattr(c.backend, 'history'):
                        c.backend.history = []
                    if hasattr(c, 'last_tools'): c.last_tools = ''
                except: pass
            display_queue.put({'done': '🧹 已清空当前会话的历史与记忆。', 'source': 'system'})
            return None, True, None

        # /session.delete <id>
        m = re.match(r'^/session\.delete\s+(.+)$', q, re.IGNORECASE)
        if m:
            target = m.group(1).strip()
            ok, msg = delete_session(target)
            icon = '✅' if ok else '❌'
            display_queue.put({'done': f'{icon} {msg}', 'source': 'system'})
            return None, True, None

        # /session.rename <标题>
        m = re.match(r'^/session\.rename\s+(.+)$', q, re.IGNORECASE) or re.match(r'^/session\s+rename\s+(.+)$', q, re.IGNORECASE)
        if m:
            title = m.group(1).strip()
            self.session.set_title(title)
            display_queue.put({'done': f'✅ 已将会话重命名为「{self.session.title}」', 'source': 'system'})
            return None, True, None

        # /session.llm [idx] / /llm.next
        if lower.startswith('/session.llm') or lower.startswith('/session llm') or lower.startswith('/llm.next') or lower.startswith('/llm next'):
            parts = raw_query.strip().split()
            idx = -1
            if len(parts) >= 2 and parts[-1].lstrip('-').isdigit():
                try: idx = int(parts[-1])
                except: idx = -1
            name = self.session.next_llm(idx)
            display_queue.put({'done': f'✅ 当前会话切换到 LLM: {name}', 'source': 'system'})
            return None, True, None

        # 兼容旧：/session.<key>=<value>
        if _sm := re.match(r'/session\.(\w+)=(.*)', raw_query.strip()):
            k, v = _sm.group(1), _sm.group(2)
            vfile = os.path.join(script_dir, 'temp', v)
            if os.path.isfile(vfile): v = open(vfile, encoding='utf-8').read().strip()
            try: v = json.loads(v)
            except (json.JSONDecodeError, ValueError): pass
            try: setattr(self.session.llmclient.backend, k, v)
            except Exception as e:
                display_queue.put({'done': f'❌ 设置属性失败: {e}', 'source': 'system'})
                return None, True, None
            display_queue.put({'done': smart_format(f"✅ session.{k} = {repr(v)}", max_str_len=500), 'source': 'system'})
            return None, True, None

        # /resume - 保持旧行为
        if raw_query.strip() == '/resume':
            return None, False, r'用re.findall(r"<history>\n\[(?:USER\|Agent)\].*?</history>", content, re.DOTALL) 扫temp/model_responses/下时间最近的10个文件(除本PID)，取每文件最后一个匹配(注意JSON里换行是字面\\n)作为该会话内容，按mtime倒序，每个用一句话总结聊了什么让我选择；选定后再简单读该文件末尾作为聊天基础'

        return None, False, raw_query

    def run_once(self, raw_query, source='user', images=None):
        """同步执行一条 query，向 `display_queue` 流式推送 chunk。"""
        display_queue = queue.Queue()
        self._execute_once(raw_query, source, images or [], display_queue)
        return display_queue

    def _execute_once(self, raw_query, source, images, display_queue):
        """单条 query 的实际执行逻辑（独立的 handler / history）。"""
        self.session.running = True
        self.session.stop_sig = False
        rquery = smart_format(raw_query.replace('\n', ' '), max_str_len=200)
        self.session.history.append(f"[USER]: {rquery}")

        sys_prompt = get_system_prompt() + getattr(self.session.llmclient.backend, 'extra_sys_prompt', '')
        handler = GenericAgentHandler(self, self.session.history, os.path.join(script_dir, 'temp'))
        if self.session.handler and 'key_info' in self.session.handler.working:
            ki = re.sub(r'\n\[SYSTEM\] 此为.*?工作记忆[。\n]*', '', self.session.handler.working['key_info'])
            handler.working['key_info'] = ki
            handler.working['passed_sessions'] = ps = self.session.handler.working.get('passed_sessions', 0) + 1
            if ps > 0: handler.working['key_info'] += f'\n[SYSTEM] 此为 {ps} 个对话前设置的key_info，若已在新任务，先更新或清除工作记忆。\n'
        self.session.handler = handler

        gen = agent_runner_loop(self.session.llmclient, sys_prompt, raw_query,
                                handler, TOOLS_SCHEMA, max_turns=70, verbose=self.session.verbose)
        try:
            full_resp = ""; last_pos = 0
            for chunk in gen:
                if consume_file(getattr(self, 'task_dir', None), '_stop'): self.abort()
                if self.session.stop_sig: break
                full_resp += chunk
                if len(full_resp) - last_pos > 50 or 'LLM Running' in chunk:
                    display_queue.put({'next': full_resp[last_pos:] if self.session.verbose else full_resp, 'source': source})
                    last_pos = len(full_resp)
            if self.session.verbose and last_pos < len(full_resp): display_queue.put({'next': full_resp[last_pos:], 'source': source})
            if '</summary>' in full_resp: full_resp = full_resp.replace('</summary>', '</summary>\n\n')
            if '</file_content>' in full_resp: full_resp = re.sub(r'<file_content>\s*(.*?)\s*</file_content>', r'\n````\n<file_content>\n\1\n</file_content>\n````', full_resp, flags=re.DOTALL)
            display_queue.put({'done': full_resp, 'source': source})
            self.session.history = handler.history_info
        except Exception as e:
            print(f"Backend Error: {format_error(e)}")
            display_queue.put({'done': (full_resp if isinstance(full_resp, str) else '') + f'\n```\n{format_error(e)}\n```', 'source': source})
        finally:
            self.session.running = False
            self.session.stop_sig = False
            if self.session.handler is not None:
                try: self.session.handler.code_stop_signal.append(1)
                except: pass

    def run_loop(self):
        """后台 worker 循环：从 session.task_queue 取任务顺序执行。"""
        while True:
            task = self.session.task_queue.get()
            try:
                raw_query, source, images, display_queue = task["query"], task["source"], task.get("images") or [], task["output"]
                new_sid, handled, _rest = self._handle_slash_cmd(raw_query, display_queue)
                if handled:
                    # 命令类直接结束；如果涉及切换会话，将剩余 query 投递到新会话
                    if new_sid:
                        try:
                            ns = get_session(new_sid, create=False) or get_session(new_sid, create=True)
                        except Exception as e:
                            display_queue.put({'done': f'❌ 切换会话失败: {e}', 'source': 'system'})
                            continue
                        # 启动新会话的 worker（如果还没有）
                        ensure_worker_for(ns)
                else:
                    self._execute_once(raw_query, source, images, display_queue)
            except Exception as e:
                try: task['output'].put({'done': f'❌ 执行异常: {e}', 'source': 'system'})
                except: pass
            finally:
                try: self.session.task_queue.task_done()
                except: pass


def _gen_session_id(hint=None):
    base = 'sess_'
    if hint:
        clean = re.sub(r'[^0-9A-Za-z_\-]+', '_', hint.strip())[:24]
        if clean: base += clean + '_'
    base += f"{int(time.time()) % 1000000:06d}_{random.randint(100, 999)}"
    return base


def ensure_worker_for(session: SessionState):
    """确保给定会话有一个后台 worker 线程处理它的任务队列。"""
    if session.worker_thread and session.worker_thread.is_alive():
        return
    agent = GeneraticAgent(session)
    t = threading.Thread(target=agent.run_loop, daemon=True, name=f"ga-worker-{session.session_id}")
    session.worker_thread = t
    t.start()


# ---- 对外便捷 API ----
def chat_once(session_id, query, source='user', images=None, create=True):
    """在指定会话中执行一次对话。返回 (display_queue, session_state)。"""
    s = get_session(session_id, create=create)
    s.touch()
    ensure_worker_for(s)
    agent = GeneraticAgent(s)
    return agent.put_task(query, source=source, images=images), s


def session_meta(session_id):
    s = get_session(session_id, create=False)
    return s.to_dict() if s else None


# ============================================================
# CLI / 任务模式 / 反射模式入口（保持向后兼容）
# ============================================================
if __name__ == '__main__':
    import argparse
    from datetime import datetime
    parser = argparse.ArgumentParser()
    parser.add_argument('--task', metavar='IODIR', help='一次性任务模式(文件IO)')
    parser.add_argument('--reflect', metavar='SCRIPT', help='反射模式：加载监控脚本，check()触发时发任务')
    parser.add_argument('--input', help='prompt')
    parser.add_argument('--llm_no', type=int, default=0)
    parser.add_argument('--verbose', action='store_true')
    parser.add_argument('--session-id', dest='session_id', default=_DEFAULT_SESSION_ID,
                        help=f'指定会话 ID（默认：{_DEFAULT_SESSION_ID}）')
    parser.add_argument('--bg', action='store_true', help='popen, print PID, exit')
    args = parser.parse_args()

    if args.bg:
        import subprocess, platform
        cmd = [sys.executable, os.path.abspath(__file__)] + [a for a in sys.argv[1:] if a != '--bg']
        d = os.path.join(script_dir, f'temp/{args.task}'); os.makedirs(d, exist_ok=True)
        p = subprocess.Popen(cmd, cwd=script_dir,
            creationflags=0x08000000 if platform.system() == 'Windows' else 0,
            stdout=open(os.path.join(d, 'stdout.log'), 'w', encoding='utf-8'),
            stderr=open(os.path.join(d, 'stderr.log'), 'w', encoding='utf-8'))
        print(p.pid); sys.exit(0)

    session = get_session(args.session_id, create=True)
    session.next_llm(args.llm_no)
    session.verbose = args.verbose
    ensure_worker_for(session)

    # CLI 交互
    if args.task:
        agent = GeneraticAgent(session)
        agent.task_dir = d = os.path.join(script_dir, f'temp/{args.task}'); nround = ''
        infile = os.path.join(d, 'input.txt')
        if args.input:
            os.makedirs(d, exist_ok=True)
            import glob; [os.remove(f) for f in glob.glob(os.path.join(d, 'output*.txt'))]
            with open(infile, 'w', encoding='utf-8') as f: f.write(args.input)
        with open(infile, encoding='utf-8') as f: raw = f.read()
        while True:
            dq = agent.put_task(raw, source='task')
            while 'done' not in (item := dq.get(timeout=120)):
                if 'next' in item and random.random() < 0.95:
                    with open(f'{d}/output{nround}.txt', 'w', encoding='utf-8') as f: f.write(item.get('next', ''))
            with open(f'{d}/output{nround}.txt', 'w', encoding='utf-8') as f: f.write(item['done'] + '\n\n[ROUND END]\n')
            consume_file(d, '_stop')
            for _ in range(300):
                time.sleep(2)
                if (raw := consume_file(d, 'reply.txt')): break
            else: break
            nround = nround + 1 if isinstance(nround, int) else 1
    elif args.reflect:
        import importlib.util
        spec = importlib.util.spec_from_file_location('reflect_script', args.reflect)
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        _mt = os.path.getmtime(args.reflect)
        print(f'[Reflect] loaded {args.reflect} (session={session.session_id})')
        agent = GeneraticAgent(session)
        while True:
            if os.path.getmtime(args.reflect) != _mt:
                try: spec.loader.exec_module(mod); _mt = os.path.getmtime(args.reflect); print('[Reflect] reloaded')
                except Exception as e: print(f'[Reflect] reload error: {e}')
            time.sleep(getattr(mod, 'INTERVAL', 5))
            try: task = mod.check()
            except Exception as e:
                print(f'[Reflect] check() error: {e}'); continue
            if task is None: continue
            print(f'[Reflect] triggered: {task[:80]}')
            dq = agent.put_task(task, source='reflect')
            try:
                while 'done' not in (item := dq.get(timeout=120)): pass
                result = item['done']
                print(result)
            except Exception as e:
                if getattr(mod, 'ONCE', False): raise
                print(f'[Reflect] drain error: {e}'); result = f'[ERROR] {e}'
            log_dir = os.path.join(script_dir, 'temp/reflect_logs'); os.makedirs(log_dir, exist_ok=True)
            script_name = os.path.splitext(os.path.basename(args.reflect))[0]
            open(os.path.join(log_dir, f'{script_name}_{datetime.now():%Y-%m-%d}.log'), 'a', encoding='utf-8').write(f'[{datetime.now():%m-%d %H:%M}]\n{result}\n\n')
            if (on_done := getattr(mod, 'on_done', None)):
                try: on_done(result)
                except Exception as e: print(f'[Reflect] on_done error: {e}')
            if getattr(mod, 'ONCE', False): print('[Reflect] ONCE=True, exiting.'); break
    else:
        try: import readline
        except Exception: pass
        print(f'[GA] 已进入 CLI 交互模式。session_id=`{session.session_id}`。输入 /session.list 查看所有会话。')
        agent = GeneraticAgent(session)
        while True:
            try: q = input('> ').strip()
            except (EOFError, KeyboardInterrupt):
                print('\nBye.'); break
            if not q: continue
            try:
                dq = agent.put_task(q, source='user')
                while True:
                    item = dq.get()
                    if 'next' in item: print(item['next'], end='', flush=True)
                    if 'done' in item: print(); break
            except KeyboardInterrupt:
                agent.abort()
                print('\n[Interrupted]')
