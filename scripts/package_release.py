"""Export an allowlisted, privacy-checked Fuuko source tree or ZIP."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from release_support import export_tree, generic_ico, generic_png, with_manifest

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = (
    '.gitignore', '.gitattributes', 'README.md', 'fuuko_app.py', 'registry.py', 'registration.py',
    'control.py', 'layout.py',
)
OPTIONAL_FILES = ('paths.py', 'LICENSE', 'NOTICE', 'docs/workbench-protocol.md')
PUBLIC_FILES = (
    'ui/index.html', 'ui/app.js', 'ui/app.css', 'ui/theme.js', 'ui/icon.svg',
    'shared/__init__.py', 'shared/wbkit.py', 'shared/wbkit.js', 'shared/fuuko.css', 'shared/README.md',
    'shared/host/FuukoHost.cs', 'shared/host/FuukoHost.exe.config',
    'shared/host/app.manifest', 'shared/host/build.ps1',
    'scripts/package_release.py', 'scripts/release_support.py', 'NOTICE.md',
    'examples/hello/workbench.json', 'examples/hello/hello_app.py', 'examples/hello/README.md',
)


def source_files(root: Path, include_host: bool = False) -> list[Path]:
    """Return only reviewed production paths; never walk private project directories."""
    root = root.resolve()
    names = list(ROOT_FILES + PUBLIC_FILES)
    names += [name for name in OPTIONAL_FILES if (root / name).is_file()]
    names += [p.relative_to(root).as_posix() for p in sorted((root / 'tests').glob('test_*.py'))]
    if include_host:
        raise ValueError('Prebuilt hosts may embed local artwork. Build the host inside the exported source tree instead.')
    paths = []
    for name in sorted(set(names)):
        path = root / name
        if not path.is_file():
            raise FileNotFoundError(f'Required release file is missing: {name}')
        chain = [path, *path.parents]
        if not path.resolve().is_relative_to(root) or any(p.is_symlink() for p in chain if p != root and p.is_relative_to(root)):
            raise ValueError(f'Release paths cannot be links: {name}')
        paths.append(path)
    return paths


def release_files(root: Path, include_host: bool = False) -> dict[str, bytes]:
    root = root.resolve()
    files = {p.relative_to(root).as_posix(): p.read_bytes() for p in source_files(root, include_host)}
    # Local artwork stays untouched. The public tree uses a code-rendered version
    # of ui/icon.svg, not the owner's profile image or an embedded private EXE.
    files.update({'shared/assets/fuuko-avatar.png': generic_png(256),
                  'shared/assets/fuuko-round.png': generic_png(256),
                  'shared/assets/fuuko.ico': generic_ico()})
    marker = b'# Generic release icons are maintained public assets.'
    if marker not in files['.gitignore']:
        files['.gitignore'] += b'\n' + marker + b'\n!/shared/assets/fuuko-avatar.png\n!/shared/assets/fuuko-round.png\n!/shared/assets/fuuko.ico\n'
    return with_manifest(files, 'fuuko')


def build_tree(root: Path, output: Path, *, refresh: bool = False) -> tuple[int, int]:
    return export_tree(release_files(root), output, refresh=refresh)


def build_zip(root: Path, output: Path, *, include_host: bool = False) -> tuple[int, int]:
    root, output = root.resolve(), output.absolute()
    if output.suffix.lower() != '.zip':
        raise ValueError('Output must be a .zip file')
    if output.exists():
        raise FileExistsError(f'Output already exists: {output}')
    files = release_files(root, include_host)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation protects an existing release. No staging tree or backup is created.
    created = False
    try:
        with output.open('xb') as handle:
            created = True
            with zipfile.ZipFile(handle, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
                for name, data in sorted(files.items()):
                    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = 0o644 << 16
                    archive.writestr(info, data)
        return len(files), output.stat().st_size
    except BaseException:
        if created:
            output.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path, help='New source directory or .zip output')
    parser.add_argument('--refresh', action='store_true', help='Refresh an unedited directory previously exported by this tool')
    parser.add_argument('--include-host', action='store_true', help='Retired: build binaries in the clean exported source tree')
    args = parser.parse_args()
    try:
        if args.include_host:
            raise ValueError('Build the optional host in the exported source tree before distributing binaries.')
        if args.output.suffix.lower() == '.zip':
            if args.refresh:
                raise ValueError('--refresh is only available for source directories')
            count, size = build_zip(ROOT, args.output)
        else:
            count, size = build_tree(ROOT, args.output, refresh=args.refresh)
    except (OSError, ValueError) as exc:
        parser.exit(1, f'{exc}\n')
    print(f'{args.output.resolve()} ({count} files, {size} bytes)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
