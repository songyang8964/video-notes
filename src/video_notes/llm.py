"""Model backends: Claude Code CLI (`claude -p`) and Codex CLI (`codex exec`).

Both reuse the user's existing CLI login; no API key is handled here. Every call is cached
by backend, model, prompt text and image content hashes, and the filled prompt, response
and stderr are kept for audit. A cached response is reused only if it completed.
"""
import hashlib
import json
import re
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from .media import sha256


class ModelError(RuntimeError):
    pass


LIMIT_HINTS = ('usage limit', 'session limit', "you've hit your", 'you’ve hit your', 'rate limit', 'quota')
AUTH_HINTS = ('failed to authenticate', 'not logged in', 'please run /login', 'oauth', 'login required',
              'unauthorized', 'usage limit', "you've hit your")


def child_env():
    """Environment for the model CLI: drop variables of any enclosing Claude Code session so the
    CLI behaves exactly as when the user runs it from their own terminal."""
    return {k: v for k, v in os.environ.items() if not (k == 'CLAUDECODE' or k.startswith('CLAUDE_'))}


def run_bounded(command, prompt, timeout, **kwargs):
    """subprocess.run with a timeout that really ends the call. On Windows, run() kills only the
    direct child and then waits for the output pipe, which a surviving grandchild of the CLI keeps
    open, so a hung call was never ended. Here the whole process tree is killed."""
    proc = subprocess.Popen(command, stdin=subprocess.PIPE, text=True, encoding='utf-8', errors='replace', **kwargs)
    try:
        stdout, _ = proc.communicate(prompt, timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'], capture_output=True)
        else:
            proc.kill()
        try:
            proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            pass
        raise
    return subprocess.CompletedProcess(command, proc.returncode, stdout, None)


def _version_key(path: Path):
    return [int(p) if p.isdigit() else p for p in re.split(r'[.\-]', path.parent.name)]


def desktop_bundles(backend):
    """CLI executables shipped inside the desktop apps (newest first). The desktop apps keep their
    own copy of the same command-line program; it still needs its own sign-in when run outside
    the app (`claude` → /login, `codex login`)."""
    home = Path.home()
    appdata = Path(os.environ.get('APPDATA', home / 'AppData/Roaming'))
    local = Path(os.environ.get('LOCALAPPDATA', home / 'AppData/Local'))
    if backend == 'claude':
        found = list((appdata / 'Claude' / 'claude-code').glob('*/claude.exe'))
        found += list((home / 'Library/Application Support/Claude/claude-code').glob('*/claude'))
        return sorted(found, key=_version_key, reverse=True)
    found = [local / 'OpenAI/Codex/bin/codex.exe', Path('/Applications/Codex.app/Contents/Resources/codex')]
    return [p for p in found if p.is_file()]


def candidates(backend):
    """(path, origin) in priority order: configured path, PATH, desktop app bundle."""
    from .config import load_config
    configured = load_config().get(f'{backend}_path')
    out = [(Path(configured), 'configured')] if configured else []
    on_path = shutil.which('claude' if backend == 'claude' else 'codex')
    if on_path:
        out.append((Path(on_path), 'PATH'))
    out += [(p, 'desktop app') for p in desktop_bundles(backend)]
    return [(p, origin) for p, origin in out if p.is_file()]


def cli_version(path):
    try:
        out = subprocess.run([str(path), '--version'], capture_output=True, text=True, timeout=30,
                             encoding='utf-8', errors='replace', env=child_env()).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ()
    match = re.search(r'(\d+)\.(\d+)\.(\d+)', out)
    return tuple(int(x) for x in match.groups()) if match else ()


def resolve(backend):
    """A configured path always wins; otherwise the newest version among PATH and desktop bundles
    (older CLIs may not recognise current model IDs)."""
    found = candidates(backend)
    if found and found[0][1] != 'configured' and len(found) > 1:
        found.sort(key=lambda item: cli_version(item[0]), reverse=True)
    if not found:
        raise ModelError(f'{backend} CLI not found (configured path, PATH or desktop app); install it, set '
                         f'`video-notes setup --{backend}-path <exe>`, or choose the other backend')
    return str(found[0][0])


def probe(backend, model=None, effort=None, timeout=180, service_tier=None):
    """One tiny real call with the same options as a real run: is the CLI installed, signed in and within
    its usage limit? → (ok, detail)"""
    try:
        exe = resolve(backend)
    except ModelError as error:
        return False, str(error)
    if backend == 'claude':
        command = [exe, '-p', '--output-format', 'text'] + CLAUDE_MINIMAL + model_args(backend, model, effort)
    else:
        command = ([exe, 'exec', '--skip-git-repo-check', '--ephemeral', '--sandbox', 'read-only']
                   + model_args(backend, model, effort)
                   + (['-c', f'service_tier="{service_tier}"'] if service_tier else []) + ['-'])
    try:
        result = subprocess.run(command, input='Reply with exactly: OK', capture_output=True, text=True,
                                encoding='utf-8', errors='replace', timeout=timeout, env=child_env())
    except subprocess.TimeoutExpired:
        return False, 'no reply within the time limit'
    output = (result.stdout + result.stderr).strip()
    if result.returncode == 0 and 'OK' in result.stdout:
        return True, 'signed in'
    return False, output[-200:] or f'exit {result.returncode}'


# A plain `claude -p` loads the whole Claude Code system prompt plus every tool and MCP server definition:
# measured ~37k context tokens per call before any content (a 200-call run spent most of its budget on it).
# Note stages need only the Read tool (to open frames), so each call starts minimal: ~1.6k tokens.
CLAUDE_MINIMAL = ['--tools', 'Read', '--allowedTools', 'Read', '--strict-mcp-config', '--disable-slash-commands',
                  '--no-session-persistence', '--system-prompt',
                  'You are a careful technical writer and reviewer. Follow the instructions in the user message '
                  'exactly. Use the Read tool only to open the files it lists.']
USAGE_KEYS = ('input_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens', 'output_tokens')


def _identity(backend, model, effort):
    return f'{backend}:{model or "default"}:{effort or "default"}'


def model_args(backend, model, effort):
    if backend == 'claude':
        return (['--model', model] if model else []) + (['--effort', effort] if effort else [])
    return (['-m', model] if model else []) + (['-c', f'model_reasoning_effort="{effort}"'] if effort else [])


class Model:
    def __init__(self, backend, cache_dir: Path, model=None, effort=None, timeout=900, log=print, fallback=None, service_tier=None, reuse_backends=()):
        if backend not in ('claude', 'codex'):
            raise ValueError('backend must be claude or codex')
        self.backend, self.model, self.effort, self.timeout, self.log = backend, model, effort, timeout, log
        self.exe = resolve(backend)
        self.cache = cache_dir
        self.cache.mkdir(parents=True, exist_ok=True)
        self.calls = 0
        self.fallback = fallback        # (backend, model, effort) taken over on a usage limit
        self.service_tier = service_tier
        self.reuse = [self.identity()] + ([_identity(*fallback)] if fallback else [])
        self.reuse += [identity for identity in (_identity(*b) for b in reuse_backends) if identity not in self.reuse]
        self._lock = threading.Lock()   # chapters run in parallel threads
        self.usage = {}                 # real token totals of new calls (claude backend)

    def identity(self):
        return _identity(self.backend, self.model, self.effort)

    def ask(self, name, prompt, images=()):
        images = [Path(p).resolve() for p in images]
        hashes = [sha256(p) for p in images]

        def cached(identity):
            key = hashlib.sha256('\0'.join([identity, prompt] + hashes).encode()).hexdigest()[:16]
            return self.cache / f'{name}-{key}.md'
        # A completed response of either backend is a finished stage: switching backends never redoes work.
        for identity in self.reuse:
            found = cached(identity)
            if found.is_file() and found.with_suffix('.ok').is_file():
                return found.read_text(encoding='utf-8')
        # One consistent backend per call: parallel chapters may switch the shared backend at any moment,
        # and the reply must be stored under the identity of the backend that actually produced it.
        with self._lock:
            state = (self.backend, self.model, self.effort, self.exe)
        identity = _identity(*state[:3])
        target = cached(identity)
        done = target.with_suffix('.ok')
        target.with_suffix('.prompt.md').write_text(prompt, encoding='utf-8')
        last_error = ''
        for attempt in range(1, 4):
            log_path = target.with_suffix(f'.attempt-{attempt}.log')
            self.log(f'model {identity} {name} attempt {attempt}, images={len(images)}')
            try:
                text = self._run(prompt, images, target, log_path, state)
            except subprocess.TimeoutExpired:
                last_error = 'timeout'
            else:
                if text.strip():
                    target.write_text(text, encoding='utf-8')
                    done.write_text('ok\n', encoding='utf-8')
                    with self._lock:
                        self.calls += 1
                    return text
                last_error = log_path.read_text(encoding='utf-8', errors='replace')[-600:]
            if any(hint in last_error.lower() for hint in LIMIT_HINTS):
                with self._lock:  # decided against this call's backend: the first thread switches, others follow
                    if self.fallback and self.backend == state[0]:
                        self.log(f'{self.backend} reached its usage limit; continuing with {self.fallback[0]} '
                                 '(completed responses of both backends are reused)')
                        self.backend, self.model, self.effort = self.fallback
                        self.exe = resolve(self.backend)
                        self.fallback = None
                    switched = self.backend != state[0]
                if switched:
                    return self.ask(name, prompt, images)
            if any(hint in last_error.lower() for hint in AUTH_HINTS):
                fix = ('run `claude` in a terminal and sign in with /login' if state[0] == 'claude'
                       else 'run `codex login`')
                raise ModelError(f'{state[0]} CLI is not usable (login expired or usage limit): '
                                 f'{last_error.strip()[-200:]} — {fix}, then rerun video-notes to resume.')
            time.sleep(10 * attempt)
        raise ModelError(f'{state[0]} failed for {name}: {last_error.strip()[-400:]} (log {log_path})')

    def record_usage(self, target, usage, cost=None):
        """Real token use per call (beside the cached response) and running totals for the report."""
        row = {k: int(usage.get(k) or 0) for k in USAGE_KEYS} | dict(cost_usd=cost or 0.0)
        target.with_suffix('.usage.json').write_text(json.dumps(row), encoding='utf-8')
        with self._lock:
            for k, v in row.items():
                self.usage[k] = self.usage.get(k, 0) + v

    def _run(self, prompt, images, target, log_path, state=None):
        backend, model, effort, exe = state or (self.backend, self.model, self.effort, self.exe)
        if backend == 'claude':
            if images:
                listing = '\n'.join(f'- {p}' for p in images)
                prompt = ('Open and look at every image file below with the Read tool before answering; '
                          'they are attachments in the stated order.\n' + listing + '\n\n' + prompt)
            command = [exe, '-p', '--output-format', 'json'] + CLAUDE_MINIMAL
            for folder in sorted({str(p.parent) for p in images}):
                command += ['--add-dir', folder]
            command += model_args('claude', model, effort)
            with log_path.open('w', encoding='utf-8') as err:
                result = run_bounded(command, prompt, self.timeout, stdout=subprocess.PIPE, stderr=err,
                                     cwd=str(self.cache), env=child_env())
            try:
                reply = json.loads(result.stdout)
            except ValueError:
                reply = {}
            if result.returncode or reply.get('is_error') or not isinstance(reply.get('result'), str):
                with log_path.open('a', encoding='utf-8') as err:
                    err.write(f'\nexit {result.returncode}\nstdout:\n{result.stdout[-2000:]}\n')
                return ''
            self.record_usage(target, reply.get('usage') or {}, reply.get('total_cost_usd'))
            return reply['result']
        command = [exe, 'exec', '--skip-git-repo-check', '--ephemeral', '--sandbox', 'read-only',
                   '-C', str(self.cache), '-o', str(target)]
        command += model_args('codex', model, effort)
        if self.service_tier:
            command += ['-c', f'service_tier="{self.service_tier}"']
        for image in images:
            command += ['-i', str(image)]
        command.append('-')
        with log_path.open('w', encoding='utf-8') as err:
            result = run_bounded(command, prompt, self.timeout, stdout=subprocess.DEVNULL, stderr=err,
                                 env=child_env())
        if result.returncode or not target.is_file():
            return ''
        return target.read_text(encoding='utf-8')
