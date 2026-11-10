"""Standard-library helpers for reviewed, reproducible source distributions."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import struct
import zlib
from functools import lru_cache

MANIFEST = 'SOURCE-MANIFEST.json'
TEXT_SUFFIXES = {'.py', '.js', '.mjs', '.json', '.md', '.html', '.css', '.svg', '.ps1', '.vbs', '.txt', '.config', '.manifest'}


def read_sources(root: Path, names: list[str]) -> dict[str, bytes]:
    root = root.resolve()
    result = {}
    for name in sorted(set(names)):
        path = root / name
        if not path.is_file():
            raise FileNotFoundError(f'Required source file is missing: {name}')
        if not path.resolve().is_relative_to(root) or any(p.is_symlink() for p in (path, *path.parents) if p != root and p.is_relative_to(root)):
            raise ValueError(f'Source paths must not be links: {name}')
        result[name] = path.read_bytes()
    return result


def audit(files: dict[str, bytes]) -> list[str]:
    """Report paths/rule names only; never echo matched private values."""
    patterns = {
        'user home path': re.compile(r'(?i)(?:[a-z]:[\\/]+Users[\\/]+[^\\/\s"\']+|/home/[a-z0-9_.-]+/)'),
        'private key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
        'credential token': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|sk-[A-Za-z0-9_-]{32,})\b'),
        'email address': re.compile(r'(?<![\w.])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b'),
    }
    # Environment values are inspection inputs, never serialized into the release.
    for label, value in (('current username', os.environ.get('USERNAME')), ('current computer', os.environ.get('COMPUTERNAME'))):
        if value and len(value) >= 5:
            patterns[label] = re.compile(re.escape(value), re.IGNORECASE)
    issues = []
    excluded = {'work', 'tmp', 'agent_records', '.git', '__pycache__', 'node_modules', 'backups', 'snapshots', 'status'}
    for name, data in files.items():
        parts = Path(name).parts
        if any(p.lower() in excluded or p.lower().startswith('private-') for p in parts):
            issues.append(f'{name}: excluded directory')
        if Path(name).name in {'AGENTS.md', 'STATUS.md', '.env', 'runtime.json', 'archive.json'}:
            issues.append(f'{name}: private/local file')
        if Path(name).suffix.lower() in TEXT_SUFFIXES or Path(name).name in {'.gitignore', '.gitattributes', 'LICENSE', 'NOTICE'}:
            text = data.decode('utf-8-sig')
            for label, pattern in patterns.items():
                matches = list(pattern.finditer(text))
                if label == 'email address':
                    # RFC-reserved domains are deliberately used in synthetic tests.
                    matches = [m for m in matches if m.group().rsplit('@', 1)[-1].lower() not in {'example.com', 'example.org', 'example.net', 'example.invalid'}]
                if matches:
                    issues.append(f'{name}: {label}')
    return issues


def with_manifest(files: dict[str, bytes], project: str) -> dict[str, bytes]:
    issues = audit(files)
    if issues:
        raise ValueError('Source audit failed:\n' + '\n'.join(issues))
    result = dict(files)
    manifest = {'schemaVersion': 1, 'project': project, 'files': [
        {'path': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
        for name, data in sorted(files.items())]}
    result[MANIFEST] = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    return result


def export_tree(files: dict[str, bytes], output: Path, *, refresh: bool = False) -> tuple[int, int]:
    """Create a new tree, or update only a complete unedited previous export."""
    output = output.absolute()
    if output.is_symlink() or any(p.is_symlink() for p in output.parents):
        raise ValueError('Export destination must not traverse a symbolic link')
    if output.exists():
        if (output / '.git').exists():
            raise ValueError('Existing export is already a Git repository; use a new destination')
        if not refresh or not (output / MANIFEST).is_file():
            raise FileExistsError(f'Output exists; use a new path or --refresh on an unedited export: {output}')
        old = json.loads((output / MANIFEST).read_text(encoding='utf-8'))
        if old.get('schemaVersion') != 1:
            raise ValueError('Unsupported existing source manifest')
        if old.get('project') != json.loads(files[MANIFEST])['project']:
            raise ValueError('Existing export belongs to another project')
        old_files = {x['path']: x['sha256'] for x in old['files']}
        actual = {p.relative_to(output).as_posix() for p in output.rglob('*') if p.is_file()}
        if actual != set(old_files) | {MANIFEST}:
            raise ValueError('Existing export has extra/missing files (including Git state); use a new destination')
        if set(old_files) - set(files):
            raise ValueError('Refresh would remove files; use a new destination')
        for name, digest in old_files.items():
            path = output / name
            if not path.resolve().is_relative_to(output.resolve()) or path.is_symlink():
                raise ValueError('Existing export has an unsafe path')
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError(f'Existing export has user edits: {name}')
    else:
        output.mkdir(parents=True)
    # Manifest goes last. An interrupted refresh fails validation on the next attempt.
    for name, data in sorted(files.items(), key=lambda item: item[0] == MANIFEST):
        destination = output / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    return len(files), sum(map(len, files.values()))


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))


@lru_cache(maxsize=16)
def generic_png(size: int, color: tuple[int, int, int] = (111, 143, 191)) -> bytes:
    """Rasterize the project's geometric star icon; never read personal artwork."""
    vertices = [(32, 9), (37.6, 24.4), (53, 30), (37.6, 35.6), (32, 51), (26.4, 35.6), (11, 30), (26.4, 24.4)]
    def inside(x, y):
        cross = False
        a, b = vertices[-1]
        for c, d in vertices:
            if (b > y) != (d > y) and x < (c - a) * (y - b) / (d - b) + a:
                cross = not cross
            a, b = c, d
        return cross
    rows = bytearray()
    for py in range(size):
        rows.append(0)
        y = (py + 0.5) * 64 / size
        for px in range(size):
            x = (px + 0.5) * 64 / size
            dx, dy = max(14 - x, 0, x - 50), max(14 - y, 0, y - 50)
            if dx * dx + dy * dy > 196:
                rows.extend((0, 0, 0, 0))
            else:
                rows.extend((255, 255, 255, 255) if inside(x, y) or (x - 48) ** 2 + (y - 48) ** 2 < 16 else (*color, 255))
    return b'\x89PNG\r\n\x1a\n' + _chunk(b'IHDR', struct.pack('>IIBBBBB', size, size, 8, 6, 0, 0, 0)) + _chunk(b'IDAT', zlib.compress(rows, 9)) + _chunk(b'IEND', b'')


def generic_ico(color: tuple[int, int, int] = (111, 143, 191)) -> bytes:
    sizes = (16, 32, 48, 64, 128, 256)
    images = [generic_png(size, color) for size in sizes]
    offset = 6 + 16 * len(sizes)
    entries = []
    for size, data in zip(sizes, images):
        entries.append(struct.pack('<BBBBHHII', size % 256, size % 256, 0, 0, 1, 32, len(data), offset))
        offset += len(data)
    return struct.pack('<HHH', 0, 1, len(sizes)) + b''.join(entries) + b''.join(images)
