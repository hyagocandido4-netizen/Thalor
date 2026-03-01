from __future__ import annotations

from pathlib import Path
import sys

FORBIDDEN = {
    0xFEFF,  # BOM / zero-width no-break space
    0x200E,  # LRM
    0x200F,  # RLM
    0x061C,  # Arabic Letter Mark
    0x202A, 0x202B, 0x202C, 0x202D, 0x202E,  # bidi embedding/override/pop
    0x2066, 0x2067, 0x2068, 0x2069,  # bidi isolates
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


def load_text(path: Path) -> str | None:
    raw = path.read_bytes()
    for enc in ('utf-8', 'utf-8-sig', 'utf-16', 'utf-16-le', 'utf-16-be'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    # If it does not decode as text, ignore it.
    return None


def main() -> int:
    root = Path.cwd()
    hits: list[str] = []

    for path in walk_repo(root):
        text = load_text(path)
        if text is None:
            continue
        rel = path.relative_to(root).as_posix()
        for lineno, line in enumerate(text.splitlines(), start=1):
            found = sorted({ord(ch) for ch in line if ord(ch) in FORBIDDEN})
            if found:
                codes = ','.join(f'U+{cp:04X}' for cp in found)
                hits.append(f'{rel}:{lineno}: {codes}')
        # Handle a forbidden char in the final empty line / BOM-only file
        if not text.splitlines() and any(ord(ch) in FORBIDDEN for ch in text):
            found = sorted({ord(ch) for ch in text if ord(ch) in FORBIDDEN})
            codes = ','.join(f'U+{cp:04X}' for cp in found)
            hits.append(f'{rel}:1: {codes}')

    if hits:
        print('Found hidden/bidi unicode control characters in:')
        for item in hits:
            print(item)
        return 1

    print('Hidden unicode / bidi guard: clean')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
