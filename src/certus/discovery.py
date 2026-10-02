"""Bounded discovery on the machine running Certus; never launches a solver."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import urllib.request


def find_abaqus():
    configured = os.environ.get('CERTUS_ABAQUS_COMMAND', '')
    if configured:
        path = Path(configured).expanduser()
        if path.is_file():
            return str(path)
        found = shutil.which(configured)
        if found:
            return found
    for name in ('abaqus', 'abaqus.bat', 'abaqus.exe'):
        found = shutil.which(name)
        if found:
            return found
    roots = [Path('C:/SIMULIA'), Path('C:/Program Files/SIMULIA'),
             Path('C:/DassaultSystemes'), Path('/opt/SIMULIA')]
    for root in roots:
        for pattern in ('Commands/abaqus.*', '*/Commands/abaqus.*',
                        '*/win_b64/code/bin/ABQLauncher.exe', 'Commands/abaqus',
                        'Commands/abq*.bat', '*/Commands/abq*.bat'):
            for path in sorted(root.glob(pattern), reverse=True):
                if path.is_file():
                    return str(path)
    return None


def server_models(base_url, timeout=1.0):
    try:
        with urllib.request.urlopen(base_url.rstrip('/') + '/models', timeout=timeout) as response:
            data = json.load(response)
        return sorted({m['id'] for m in data.get('data', [])
                       if isinstance(m.get('id'), str) and m['id']})
    except (OSError, ValueError, KeyError, TypeError):
        return []


def scan_local_models():
    servers = {'Ollama': 'http://localhost:11434/v1',
               'LM Studio': 'http://localhost:1234/v1',
               'llama.cpp': 'http://localhost:8080/v1'}
    with ThreadPoolExecutor(max_workers=3) as pool:
        models = list(pool.map(server_models, servers.values()))
    result = [{'name': name, 'url': url, 'models': available}
              for (name, url), available in zip(servers.items(), models)]
    # Ollama stores model manifests separately from its content-addressed blobs.
    cache = Path(os.environ.get('OLLAMA_MODELS', str(Path.home() / '.ollama/models')))
    manifests = cache / 'manifests'
    installed = []
    if manifests.is_dir():
        for path in manifests.glob('*/*/*/*'):
            if path.is_file():
                parts = path.relative_to(manifests).parts
                installed.append(f'{parts[-2]}:{parts[-1]}')
    files = []
    for root in (Path.home() / '.lmstudio/models', Path.home() / '.cache/lm-studio/models'):
        if root.is_dir():
            files.extend(str(p.relative_to(root)) for p in root.glob('*/*/*.gguf') if p.is_file())
    return {'servers': result, 'ollama_installed': sorted(set(installed)),
            'lmstudio_files': sorted(set(files)),
            'runtimes': [name for name in ('ollama', 'lms', 'llama-server')
                         if shutil.which(name)]}
