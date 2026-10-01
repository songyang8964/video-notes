"""Model backends: Claude Code CLI (`claude -p`) and Codex CLI (`codex exec`).

Both reuse the user's existing CLI login; no API key is handled here. Every call is cached
by backend, model, prompt text and image content hashes, and the filled prompt, response
and stderr are kept for audit. A cached response is reused only if it completed.
"""
import hashlib
import re
import os
import shutil
import subprocess
import time
from pathlib import Path

from .media import sha256


class ModelError(RuntimeError):
    pass


AUTH_HINTS = ('failed to authenticate', 'not logged in', 'please run /login', 'oauth', 'login required',
              'unauthorized', 'usage limit', "you've hit your")


def child_env():
    """Environment for the model CLI: drop variables of any enclosing Claude Code session so the
    CLI behaves exactly as when the user runs it from their own terminal."""
    return {k: v for k, v in os.environ.items() if not (k == 'CLAUDECODE' or k.startswith('CLAUDE_'))}


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


def probe(backend, model=None, effort=None, timeout=180):
    """One tiny real call: is the CLI installed, signed in and within its usage limit? → (ok, detail)."""
    try:
        exe = resolve(backend)
    except ModelError as error:
        return False, str(error)
    if backend == 'claude':
        command = [exe, '-p', '--output-format', 'text'] + model_args(backend, model, effort)
    else:
        command = ([exe, 'exec', '--skip-git-repo-check', '--ephemeral', '--sandbox', 'read-only']
                   + model_args(backend, model, effort) + ['-'])
    try:
        result = subprocess.run(command, input='Reply with exactly: OK', capture_output=True, text=True,
                                encoding='utf-8', errors='replace', timeout=timeout, env=child_env())
    except subprocess.TimeoutExpired:
        return False, 'no reply within the time limit'
    output = (result.stdout + result.stderr).strip()
    if result.returncode == 0 and 'OK' in result.stdout:
        return True, 'signed in'
    return False, output[-200:] or f'exit {result.returncode}'


def model_args(backend, model, effort):
    if backend == 'claude':
        return (['--model', model] if model else []) + (['--effort', effort] if effort else [])
    return (['-m', model] if model else []) + (['-c', f'model_reasoning_effort="{effort}"'] if effort else [])


class Model:
    def __init__(self, backend, cache_dir: Path, model=None, effort=None, timeout=1800, log=print):
        if backend not in ('claude', 'codex'):
            raise ValueError('backend must be claude or codex')
        self.backend, self.model, self.effort, self.timeout, self.log = backend, model, effort, timeout, log
        self.exe = resolve(backend)
        self.cache = cache_dir
        self.cache.mkdir(parents=True, exist_ok=True)
        self.calls = 0

    def identity(self):
        return f'{self.backend}:{self.model or "default"}:{self.effort or "default"}'

    def ask(self, name, prompt, images=()):
        images = [Path(p).resolve() for p in images]
        key = hashlib.sha256('\0'.join([self.identity(), prompt] + [sha256(p) for p in images]).encode()).hexdigest()[:16]
        target = self.cache / f'{name}-{key}.md'
        done = target.with_suffix('.ok')
        if target.is_file() and done.is_file():
            return target.read_text(encoding='utf-8')
        target.with_suffix('.prompt.md').write_text(prompt, encoding='utf-8')
        last_error = ''
        for attempt in range(1, 4):
            log_path = target.with_suffix(f'.attempt-{attempt}.log')
            self.log(f'model {self.identity()} {name} attempt {attempt}, images={len(images)}')
            try:
                text = self._run(prompt, images, target, log_path)
            except subprocess.TimeoutExpired:
                last_error = 'timeout'
            else:
                if text.strip():
                    target.write_text(text, encoding='utf-8')
                    done.write_text('ok\n', encoding='utf-8')
                    self.calls += 1
                    return text
                last_error = log_path.read_text(encoding='utf-8', errors='replace')[-600:]
            if any(hint in last_error.lower() for hint in AUTH_HINTS):
                fix = ('run `claude` in a terminal and sign in with /login' if self.backend == 'claude'
                       else 'run `codex login`')
                raise ModelError(f'{self.backend} CLI is not usable (login expired or usage limit): '
                                 f'{last_error.strip()[-200:]} — {fix}, then rerun video-notes to resume.')
            time.sleep(10 * attempt)
        raise ModelError(f'{self.backend} failed for {name}: {last_error.strip()[-400:]} (log {log_path})')

    def _run(self, prompt, images, target, log_path):
        if self.backend == 'claude':
            if images:
                listing = '\n'.join(f'- {p}' for p in images)
                prompt = ('Open and look at every image file below with the Read tool before answering; '
                          'they are attachments in the stated order.\n' + listing + '\n\n' + prompt)
            command = [self.exe, '-p', '--output-format', 'text', '--allowedTools', 'Read']
            for folder in sorted({str(p.parent) for p in images}):
                command += ['--add-dir', folder]
            command += model_args('claude', self.model, self.effort)
            with log_path.open('w', encoding='utf-8') as err:
                result = subprocess.run(command, input=prompt, stdout=subprocess.PIPE, stderr=err, text=True,
                                        encoding='utf-8', errors='replace', timeout=self.timeout,
                                        cwd=str(self.cache), env=child_env())
            if result.returncode:
                with log_path.open('a', encoding='utf-8') as err:
                    err.write(f'\nexit {result.returncode}\nstdout:\n{result.stdout[-2000:]}\n')
                return ''
            return result.stdout
        command = [self.exe, 'exec', '--skip-git-repo-check', '--ephemeral', '--sandbox', 'read-only',
                   '-C', str(self.cache), '-o', str(target)]
        command += model_args('codex', self.model, self.effort)
        for image in images:
            command += ['-i', str(image)]
        command.append('-')
        with log_path.open('w', encoding='utf-8') as err:
            result = subprocess.run(command, input=prompt, stdout=subprocess.DEVNULL, stderr=err, text=True,
                                    encoding='utf-8', errors='replace', timeout=self.timeout, env=child_env())
        if result.returncode or not target.is_file():
            return ''
        return target.read_text(encoding='utf-8')
