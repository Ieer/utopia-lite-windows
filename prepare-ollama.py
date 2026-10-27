import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile


VERSION = '0.40.3'
URL = f'https://github.com/ollama/ollama/releases/download/v{VERSION}/ollama-windows-amd64.zip'
ARCHIVE_SIZE = 1468053171
ARCHIVE_SHA256 = 'e5ecff02dfc7cae8ddf6d6b7a7ac5f727ffeedf4491f44ae75ff5b0820dbbbd5'


class RemoteArchive(io.RawIOBase):
    def __init__(self):
        self.position = 0
        self.download_url = None
        self.cache = Path(tempfile.gettempdir()) / f'utopia-ollama-{VERSION}-ranges'
        self.cache.mkdir(exist_ok=True)

    def seekable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=os.SEEK_SET):
        origin = {os.SEEK_SET: 0, os.SEEK_CUR: self.position, os.SEEK_END: ARCHIVE_SIZE}[whence]
        self.position = origin + offset
        if self.position < 0:
            raise ValueError('Negative archive offset')
        return self.position

    def read(self, size=-1):
        size = min(size if size >= 0 else ARCHIVE_SIZE, ARCHIVE_SIZE - self.position, 256 * 1024)
        if size <= 0:
            return b''
        end = self.position + size - 1
        cached = self.cache / f'{self.position}-{end}.bin'
        if cached.exists() and cached.stat().st_size == size:
            data = cached.read_bytes()
        else:
            for attempt in range(3):
                try:
                    expected = f'bytes {self.position}-{end}/{ARCHIVE_SIZE}'
                    curl = shutil.which('curl.exe')
                    if curl:
                        if self.download_url is None:
                            redirect = subprocess.run([curl, '--location', '--fail', '--silent', '--show-error',
                                                       '--head', '--connect-timeout', '15', '--max-time', '60',
                                                       '--output', os.devnull, '--write-out', '%{url_effective}', URL],
                                                      capture_output=True, check=True)
                            resolved = redirect.stdout.decode().strip()
                            if not resolved.startswith('https://release-assets.githubusercontent.com/'):
                                raise RuntimeError('Unexpected official asset redirect')
                            self.download_url = resolved
                        headers = self.cache / f'{self.position}-{end}.headers'
                        result = subprocess.run([curl, '--location', '--fail', '--silent', '--show-error',
                                                 '--range', f'{self.position}-{end}', '--connect-timeout', '15', '--max-time', '180',
                                                 '--dump-header', str(headers), self.download_url], capture_output=True, check=True)
                        if f'content-range: {expected}' not in headers.read_text().lower():
                            raise RuntimeError('Server did not honor the requested ZIP byte range')
                        data = result.stdout
                    else:
                        request = urllib.request.Request(URL, headers={'Range': f'bytes={self.position}-{end}'})
                        with urllib.request.urlopen(request, timeout=180) as response:
                            if response.status != 206 or response.headers.get('Content-Range') != expected:
                                raise RuntimeError('Server did not honor the requested ZIP byte range')
                            data = response.read()
                    if len(data) != size:
                        raise RuntimeError('Incomplete ZIP byte range')
                    cached.write_bytes(data)
                    break
                except (OSError, RuntimeError, subprocess.CalledProcessError):
                    if attempt == 2:
                        raise
        self.position += size
        return data


def keep_file(name):
    if name == 'ollama.exe':
        return True
    if not name.startswith('lib/ollama/') or name.count('/') != 2:
        return False
    basename = name.rsplit('/', 1)[1]
    if basename.startswith('ggml-cpu-'):
        return basename in {'ggml-cpu-x64.dll', 'ggml-cpu-haswell.dll'}
    return bool(basename)


def main():
    parser = argparse.ArgumentParser(description='Prepare the official Ollama CPU-only portable runtime.')
    parser.add_argument('--archive', type=Path, help='Optional full official ZIP; SHA-256 will be verified.')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    destination = root / 'ollama'
    if destination.exists():
        raise RuntimeError('ollama already exists; use a fresh package directory to avoid overwriting it')
    if args.archive:
        with args.archive.open('rb') as source:
            digest = hashlib.file_digest(source, 'sha256').hexdigest()
        if digest != ARCHIVE_SHA256:
            raise RuntimeError('Official archive SHA-256 mismatch')
        source_archive = args.archive
    else:
        source_archive = RemoteArchive()
    with tempfile.TemporaryDirectory(prefix='.ollama-prepare-', dir=root) as staging:
        runtime = Path(staging) / 'ollama'
        runtime.mkdir()
        hashes = {}
        with zipfile.ZipFile(source_archive) as archive:
            for entry in archive.infolist():
                if entry.is_dir() or not keep_file(entry.filename):
                    continue
                print(f'Extracting {entry.filename} ({entry.file_size} bytes)', flush=True)
                data = archive.read(entry)
                target = runtime / entry.filename
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                hashes[entry.filename] = hashlib.sha256(data).hexdigest()
        if 'ollama.exe' not in hashes or 'lib/ollama/ggml-cpu-x64.dll' not in hashes:
            raise RuntimeError('Required CPU runtime files are missing')
        metadata = {'version': VERSION, 'source': URL, 'official_archive_sha256': ARCHIVE_SHA256,
                    'validation': 'full archive SHA-256 and ZIP CRC' if args.archive else 'HTTPS byte ranges and ZIP entry CRC (not full archive SHA-256)',
                    'files': hashes}
        (runtime / 'SOURCE.json').write_text(json.dumps(metadata, indent=2) + '\n', encoding='utf-8')
        runtime.rename(destination)
    print(f'CPU-only runtime prepared: {destination}')


if __name__ == '__main__':
    main()