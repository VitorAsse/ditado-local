"""Provision the CUDA 12/cuDNN 8 DLLs required by the pinned CTranslate2 4.4.0.

Official NVIDIA PyPI wheels, immutable hashes; no global PATH or driver changes.
Run with the app closed if replacing existing libraries.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import urllib.request
import zipfile

WHEELS = (
    ("nvidia-cublas-cu12", "12.4.5.8",
     "https://files.pythonhosted.org/packages/e2/2a/4f27ca96232e8b5269074a72e03b4e0d43aa68c9b965058b1684d07c6ff8/nvidia_cublas_cu12-12.4.5.8-py3-none-win_amd64.whl",
     "5a796786da89203a0657eda402bcdcec6180254a8ac22d72213abc42069522dc"),
    ("nvidia-cudnn-cu12", "8.9.7.29",
     "https://files.pythonhosted.org/packages/25/87/b378ca9ac1f91ff68dadf517f9f804a5992315dfa5001efc46fc5966fa4d/nvidia_cudnn_cu12-8.9.7.29-py3-none-win_amd64.whl",
     "4447321a2bdc8bd965084c1824575eb04f47a03ab62bbeb6cce7e9f74c3657f3"),
)


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def install(destination, wheel_cache=None):
    destination = Path(destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ditado-gpu-', dir=destination.parent) as directory:
        stage = Path(directory)
        payload = stage / 'payload'
        payload.mkdir()
        manifest = {'ctranslate2': '4.4.0', 'packages': [], 'files': {}}
        for name, version, url, digest in WHEELS:
            wheel_name = url.rsplit('/', 1)[-1]
            cached = Path(wheel_cache) / wheel_name if wheel_cache else None
            wheel = cached if cached and cached.is_file() else stage / wheel_name
            if not wheel.is_file():
                print('Baixando ' + name + ' ' + version, flush=True)
                with urllib.request.urlopen(url, timeout=120) as response, wheel.open('wb') as output:
                    shutil.copyfileobj(response, output)
            if sha256(wheel) != digest:
                raise RuntimeError('SHA-256 incorreto: ' + wheel_name)
            manifest['packages'].append({'name': name, 'version': version, 'wheel_sha256': digest})
            with zipfile.ZipFile(wheel) as archive:
                for entry in archive.infolist():
                    base = Path(entry.filename).name
                    if base.lower().endswith('.dll'):
                        target = payload / base
                    elif base.lower().startswith(('license', 'notice')) and not entry.is_dir():
                        target = payload / (name + '-' + base)
                    else:
                        continue
                    target.write_bytes(archive.read(entry))
                    manifest['files'][target.name] = sha256(target)
        required = {'cublas64_12.dll', 'cublasLt64_12.dll', 'cudnn_ops_infer64_8.dll', 'cudnn_cnn_infer64_8.dll'}
        if not required <= set(manifest['files']):
            raise RuntimeError('O pacote não contém todas as DLLs necessárias.')
        destination.mkdir(parents=True, exist_ok=True)
        for source in payload.iterdir():
            target = destination / source.name
            if target.is_file() and sha256(target) == manifest['files'][source.name]:
                continue
            # All archives were verified before modifying the destination.
            os.replace(source, target)
        (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print('Bibliotecas verificadas em ' + str(destination))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path, default=Path(os.environ['LOCALAPPDATA']) / 'faster-whisper' / 'gpu-libs')
    parser.add_argument('--wheel-cache', type=Path)
    arguments = parser.parse_args()
    install(arguments.destination, arguments.wheel_cache)
