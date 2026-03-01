from __future__ import annotations

from pathlib import Path
import sys

FORBIDDEN = {
    0xFEFF, 0x200E, 0x200F, 0x061C,
    0x202A, 0x202B, 0x202C, 0x202D, 0x202E,
    0x2066, 0x2067, 0x2068, 0x2069,
}

TEXT_NAMES = {'.gitignore', '.gitattributes', '.editorconfig'}
TEXT_SUFFIXES = {
    '.py', '.ps1', '.md', '.yml', '.yaml', '.toml', '.cfg', '.ini', '.txt',
    '.json', '.sql', '.csv', '.psm1', '.psd1', '.bat', '.cmd', '.sh'
}
SKIP_DIRS = {'.git', '.venv', '__pycache__', 'runs', 'data', '.mypy_cache', '.pytest_cache'}


def is_text_candidate(path: Path) -> bool:
    if path.name in TEXT_NAMES:
        return True
    return path.suffix.lower() in TEXT_SUFFIXES


def walk_repo(root: Path):
    for path in root.rglob('*'):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if is_text_candidate(path):
            yield path


def load_text(path: Path) -> tuple[str | None, str | None]:
    raw = path.read_bytes()
    for enc in ('utf-8', 'utf-8-sig', 'utf-16', 'utf-16-le', 'utf-16-be'):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return None, None


def main() -> int:
    root = Path.cwd()
    changed: list[str] = []

    for path in walk_repo(root):
        text, _enc = load_text(path)
        if text is None:
            continue
        cleaned = ''.join(ch for ch in text if ord(ch) not in FORBIDDEN)
        if cleaned != text:
            path.write_text(cleaned, encoding='utf-8', newline='')
            changed.append(path.relative_to(root).as_posix())

    if changed:
        print('Removed hidden/bidi unicode control characters from:')
        for item in changed:
            print(item)
    else:
        print('No hidden/bidi unicode control characters found.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
