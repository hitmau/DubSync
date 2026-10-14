from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import os
import time


SKIP_DIRS = {
    "temp", "cache", "shadercache", "crashreports", "crashes", "logs",
    "screenshots", "savedgames", "saved games", "__pycache__", ".git",
}
MAX_INVENTORY_FILES = 150_000
MAX_BINARY_CANDIDATES = 72
MAX_BYTES_PER_BINARY = 96 * 1024 * 1024
MAX_TOTAL_BINARY_BYTES = 768 * 1024 * 1024


@dataclass(slots=True, frozen=True)
class FileRecord:
    relative_path: str
    name: str
    suffix: str
    size: int


@dataclass(slots=True)
class ScanContext:
    root: Path
    executable: Path | None
    game_name: str
    files: list[FileRecord] = field(default_factory=list)
    token_matches: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def matching_files(self, *needles: str) -> list[FileRecord]:
        normalized = tuple(item.casefold() for item in needles)
        return [item for item in self.files
                if any(needle in item.relative_path.casefold() for needle in normalized)]

    def named_files(self, *names: str) -> list[FileRecord]:
        wanted = {item.casefold() for item in names}
        return [item for item in self.files if item.name.casefold() in wanted]

    def token_evidence(self, *tokens: str) -> list[tuple[str, list[str]]]:
        found: dict[str, list[str]] = {}
        for token in tokens:
            for path, matched in self.token_matches.get(token.casefold(), []):
                found.setdefault(path, []).append(matched)
        return [(path, sorted(set(matches), key=str.casefold))
                for path, matches in found.items()]


def resolve_target(value: str | Path) -> tuple[Path, Path | None]:
    target = Path(value).expanduser().resolve(strict=True)
    if target.is_file():
        if target.suffix.casefold() not in {".exe", ".app", ".bin"}:
            raise ValueError("Selecione uma pasta do jogo ou um executável do jogo.")
        return target.parent, target
    if not target.is_dir():
        raise ValueError("O caminho selecionado não é uma pasta ou um executável.")
    executable = None
    try:
        candidates = [item for item in target.iterdir()
                      if item.is_file() and not item.is_symlink() and item.suffix.casefold() == ".exe"]
        executable = next((item for item in candidates
                           if item.stem.casefold() == target.name.casefold()), None)
        if executable is None and len(candidates) == 1:
            executable = candidates[0]
    except OSError:
        pass
    return target, executable


def build_inventory(context: ScanContext, progress=None) -> None:
    started = time.monotonic()
    files: list[FileRecord] = []
    root = context.root
    for current, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        dirnames[:] = [name for name in dirnames
                       if name.casefold() not in SKIP_DIRS
                       and not (current_path / name).is_symlink()]
        for name in filenames:
            path = current_path / name
            try:
                if path.is_symlink() or not path.is_file():
                    continue
                stat = path.stat()
                relative = path.relative_to(root).as_posix()
                files.append(FileRecord(relative, name, path.suffix.casefold(), stat.st_size))
            except (OSError, ValueError):
                continue
            if len(files) >= MAX_INVENTORY_FILES:
                context.warnings.append(
                    f"Inventário limitado a {MAX_INVENTORY_FILES:,} arquivos; o restante não foi percorrido."
                )
                break
        if len(files) >= MAX_INVENTORY_FILES:
            break
        if progress:
            progress(len(files))
    context.files = files
    if time.monotonic() - started > 30:
        context.warnings.append("O inventário levou mais de 30 segundos; pastas temporárias foram ignoradas.")


def scan_binary_tokens(context: ScanContext, tokens: set[str], progress=None) -> None:
    """Procura somente nomes de assinatura em arquivos; nunca executa conteúdo do jogo."""
    ranked: list[FileRecord] = []
    for record in context.files:
        rel = record.relative_path.casefold()
        if record.suffix not in {".dll", ".exe", ".dat", ".json", ".xml", ".txt", ".csv", ".bytes"}:
            continue
        if any(word in rel for word in ("assembly", "managed", "metadata", "dialog", "subtitle",
                                         "articy", "localiz", "narrative", "string", "culture")):
            ranked.insert(0, record)
        elif record.suffix in {".dll", ".dat"}:
            ranked.append(record)
    selected = ranked[:MAX_BINARY_CANDIDATES]
    token_bytes = {token.casefold(): (token.casefold().encode("utf-8"),
                                      token.casefold().encode("utf-16le")) for token in tokens}
    total_read = 0
    truncated = False
    for index, record in enumerate(selected):
        if total_read >= MAX_TOTAL_BINARY_BYTES:
            truncated = True
            break
        path = context.root / Path(record.relative_path)
        per_file = min(record.size, MAX_BYTES_PER_BINARY,
                       MAX_TOTAL_BINARY_BYTES - total_read)
        if per_file <= 0:
            continue
        try:
            found: set[str] = set()
            overlap = b""
            read = 0
            with path.open("rb") as stream:
                while read < per_file:
                    block = stream.read(min(1024 * 1024, per_file - read))
                    if not block:
                        break
                    read += len(block)
                    haystack = (overlap + block).lower()
                    for token, encodings in token_bytes.items():
                        if token in found:
                            continue
                        if any(encoding in haystack for encoding in encodings):
                            found.add(token)
                    overlap = haystack[-512:]
            total_read += read
            for token in found:
                context.token_matches.setdefault(token, []).append((record.relative_path, token))
        except OSError:
            continue
        if progress:
            progress(index + 1, len(selected))
        if record.size > per_file:
            truncated = True
    if truncated:
        context.warnings.append(
            "A busca de assinaturas foi limitada por tamanho e tempo; uma assinatura ausente não prova que a tecnologia não exista."
        )
