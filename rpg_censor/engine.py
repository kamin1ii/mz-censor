"""Loose RPG Maker MV/MZ image discovery and reversible PNG encryption."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

HEADER = bytes.fromhex("5250474d560000000003010000000000")
PNG = b"\x89PNG\r\n\x1a\n"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".rpgmvp", ".png_",
                    ".bmp", ".gif", ".tif", ".tiff", ".ico", ".avif", ".svg", ".apng"}


def safe_path(root: Path, relative: str) -> Path:
    """Reject traversal, drive names, ADS and symlink escapes."""
    if not relative or "\\" in relative or ":" in relative:
        raise ValueError(f"Invalid asset path: {relative!r}")
    target = (root / relative).resolve()
    if target == root.resolve() or not target.is_relative_to(root.resolve()):
        raise ValueError(f"Asset path escapes its folder: {relative!r}")
    return target


def decrypt_image(data: bytes, key: str) -> bytes:
    if data.startswith(PNG):
        return data
    if not data.startswith(HEADER) or len(data) < 32:
        raise ValueError("Not a supported RPG Maker encrypted PNG")
    secret = bytes.fromhex(key)
    if len(secret) != 16:
        raise ValueError("RPG Maker image encryption requires a 16-byte key")
    result = bytes(a ^ b for a, b in zip(data[16:32], secret)) + data[32:]
    if not result.startswith(PNG):
        raise ValueError("Incorrect image encryption key")
    return result


def encrypt_image(png: bytes, key: str) -> bytes:
    secret = bytes.fromhex(key)
    if len(secret) != 16 or not png.startswith(PNG) or len(png) < 16:
        raise ValueError("Expected PNG data and a 16-byte encryption key")
    return HEADER + bytes(a ^ b for a, b in zip(png[:16], secret)) + png[16:]


@dataclass(frozen=True)
class Game:
    root: Path
    engine: str
    title: str
    encryption_key: str


def detect_game(selected: str | Path) -> Game:
    selected = Path(selected).resolve()
    if selected.is_file():
        selected = selected.parent
    for root in (selected, selected / "www"):
        system_file = root / "data/System.json"
        if not system_file.is_file():
            continue
        system = json.loads(system_file.read_text(encoding="utf-8-sig"))
        engine = "MZ" if (root / "js/rmmz_core.js").is_file() else "MV"
        return Game(root, engine, system.get("gameTitle", root.name), system.get("encryptionKey", ""))
    raise ValueError("No RPG Maker data/System.json found. For a packed game, use Import Packed EXE first.")


def image_paths(game: Game) -> list[Path]:
    inventory = game.root / 'inventory.json'
    if inventory.exists():
        rows = json.loads(inventory.read_text(encoding='utf-8'))
        return sorted(safe_path(game.root, row['path']) for row in rows if row['image'])
    found = []
    for file in game.root.rglob('*'):
        if not file.is_file() or any(part in {'save', '.rpg-censor', 'censor-images'}
                                    for part in file.relative_to(game.root).parts):
            continue
        safe_path(game.root, file.relative_to(game.root).as_posix())
        with file.open('rb') as stream:
            header = stream.read(32)
        if header.startswith(HEADER):
            try:
                header = decrypt_image(header, game.encryption_key)
            except ValueError:
                header = b''  # Stock encrypted audio uses the same outer header.
        image_magic = header.startswith((PNG, b'\xff\xd8\xff', b'GIF87a', b'GIF89a', b'BM', b'\x00\x00\x01\x00', b'II*\x00', b'MM\x00*'))
        image_magic |= header.startswith(b'RIFF') and header[8:12] == b'WEBP'
        if file.suffix.lower() in IMAGE_EXTENSIONS or image_magic:
            found.append(file)
    return sorted(found)
