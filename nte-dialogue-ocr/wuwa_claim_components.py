"""The Windows x64 / CPython 3.12 wheels shared by the installer and release builder."""
import hashlib
from pathlib import Path
import subprocess
import tempfile

DRIVER = '1.65.2'
DIRECTORY = 'tools/breezeblue-wheels/' + DRIVER
WHEELS = {
    'greenlet-3.5.6-cp312-cp312-win_amd64.whl': '7ac4abb3877c43af320392c664774eef6fa2cc063c79a55fc02d844a3cbe7395',
    'playwright-1.65.2-py3-none-win_amd64.whl': 'd0aec9e1c97b6f497f3d9f2ad6e1b7f299968e61239d71f100298b0690f3c776',
}
INDEXES = ('https://pypi.tuna.tsinghua.edu.cn/simple', 'https://pypi.org/simple')


def verified_wheels(directory):
    paths = []
    for name, expected in WHEELS.items():
        path = Path(directory) / name
        if not path.is_file():
            raise ValueError('领取组件不完整，请重新解压便携包。缺少 ' + name)
        with path.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
                raise ValueError('领取组件校验失败，请重新下载 · ' + name)
        paths.append(path)
    return paths


def fetch_wheels(python, directory):
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        lock = Path(tmp) / 'requirements.txt'
        lock.write_text(''.join(f'{n.split("-")[0]}=={n.split("-")[1]} --hash=sha256:{sha}\n'
                                for n, sha in WHEELS.items()), encoding='ascii')
        for index in INDEXES:
            try:
                proc = subprocess.run([str(python), '-m', 'pip', '--isolated', 'download',
                    '--disable-pip-version-check', '--require-hashes', '--only-binary=:all:', '--no-deps',
                    '--platform', 'win_amd64', '--python-version', '312', '--implementation', 'cp', '--abi', 'cp312',
                    '--retries', '1', '--timeout', '15', '--index-url', index, '-r', str(lock),
                    '--dest', str(directory)], capture_output=True, timeout=150,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            except subprocess.TimeoutExpired:
                detail = '连接超时'
                continue
            if proc.returncode == 0:
                return verified_wheels(directory)
            errors = proc.stderr.decode('utf8', 'replace').splitlines()
            detail = next((s for s in reversed(errors) if 'ERROR:' in s), '下载失败')
    raise ValueError('无法下载领取组件。可以使用完整便携包中的 ' + DIRECTORY + '。' + detail[:160])
