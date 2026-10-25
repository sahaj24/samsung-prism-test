#!/usr/bin/env python3
"""Download pinned official text weights and llama.cpp CUDA 12.8 binaries locally."""
import hashlib
import os
import secrets
from pathlib import Path
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / 'runs/local-planner'
ASSETS = [
    ('gemma-4-E2B_q4_0-it.gguf',
     'https://huggingface.co/google/gemma-4-E2B-it-qat-q4_0-gguf/resolve/675cff42a74c774d6cb76f76d8eacb49b48c9b93/gemma-4-E2B_q4_0-it.gguf',
     'fa401b55b07ee70a54c6dae3903c783a6e65064312529ea57175cb5f8dec6634'),
    ('llama-b11753-bin-ubuntu-cuda-12.8-x64.tar.gz',
     'https://github.com/ggml-org/llama.cpp/releases/download/b11753/llama-b11753-bin-ubuntu-cuda-12.8-x64.tar.gz',
     '9aa8ebb82460169db37983a73d6ce1687a68fa127437b5f94cebc6f20075d2ba'),
    ('cudart-llama-b11753-bin-ubuntu-cuda-12.8-x64.tar.gz',
     'https://github.com/ggml-org/llama.cpp/releases/download/b11753/cudart-llama-b11753-bin-ubuntu-cuda-12.8-x64.tar.gz',
     'bf58eda480f73682f446d12d820648c3f772687a26a291ffe0af475dca8dd18f'),
]


def sha256(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(8*1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()


def main():
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    for name, url, expected in ASSETS:
        path = DIRECTORY / name
        if not path.exists():
            partial = path.with_suffix(path.suffix + '.part')
            subprocess.run(['curl', '--fail', '--location', '--retry', '3', '--connect-timeout', '20',
                            '--max-time', '1800', '--continue-at', '-', '--output', str(partial), url], check=True)
            if sha256(partial) != expected:
                raise SystemExit(f'Checksum mismatch: {partial}; preserved for inspection')
            partial.rename(path)
        if sha256(path) != expected:
            raise SystemExit(f'Checksum mismatch: {path}; refusing to overwrite')
        print(f'Verified {name}', flush=True)
    runtime = DIRECTORY / 'runtime'
    if not runtime.exists():
        staging = DIRECTORY / 'runtime.partial'
        staging.mkdir(exist_ok=True)
        for name, _, _ in ASSETS[1:]:
            with tarfile.open(DIRECTORY/name) as archive:
                # Requires Python 3.12, matching the project's pinned launch environment.
                archive.extractall(staging, filter='data')
        staging.rename(runtime)
    servers = list(runtime.rglob('llama-server'))
    if len(servers) != 1:
        raise SystemExit('Cannot uniquely locate llama-server; inspect runtime directory')
    libraries = sorted({str(p.parent) for p in runtime.rglob('*.so*')})
    key_file = DIRECTORY / 'api-key'
    if not key_file.exists():
        fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write(secrets.token_urlsafe(32) + '\n')
    launcher = DIRECTORY / 'serve.sh'
    import shlex
    launcher.write_text('#!/usr/bin/env bash\nset -euo pipefail\n'
        + 'export LD_LIBRARY_PATH=' + shlex.quote(':'.join(libraries)) + ':${LD_LIBRARY_PATH:-}\n'
        + 'exec ' + shlex.quote(str(servers[0])) + ' -m ' + shlex.quote(str(DIRECTORY/ASSETS[0][0]))
        + ' --api-key-file ' + shlex.quote(str(key_file))
        + ' --cors-origins http://127.0.0.1:8081 --alias reprise-gemma-e2b --host 127.0.0.1 --port 8081 --jinja'
        + ' -c 8192 -ngl "${REPRISE_GPU_LAYERS:-20}" -b 128 -ub 128 -np 1 -t 4 "$@"\n')
    os.chmod(launcher, 0o700)
    print(f'Run {launcher}. No server has been started.')


if __name__ == '__main__':
    main()
