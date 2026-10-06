"""RPG Maker project metadata; shared layer definitions stay upstream-compatible."""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path

from alice_censor.project import ProjectState
from .grouping import scene_groups, scene_key
from .engine import HEADER, decrypt_image, detect_game, image_paths, safe_path
from .image_decode import validated_image


@dataclass
class RpgProject(ProjectState):
    kind: str = "rpgmaker-censor"
    game_root: str = ""
    engine: str = ""
    title: str = ""
    encryption_key: str = ""
    assets: dict = field(default_factory=dict)
    packed: bool = False

    def to_dict(self):
        return {**super().to_dict(), **{name: getattr(self, name) for name in
                ("kind", "game_root", "engine", "title", "encryption_key", "assets", "packed")}}

    @classmethod
    def from_dict(cls, data):
        if data.get("kind") != "rpgmaker-censor":
            raise ValueError("This is not an RPG Maker Censor project")
        base = ProjectState.from_dict(data)
        project = cls(**vars(base), **{name: data[name] for name in
                      ("game_root", "engine", "title", "encryption_key", "assets", "packed")})
        for name, asset in project.assets.items():
            safe_path(Path(project.extract_dir), name)
            safe_path(Path(project.game_root), asset["source"])
        if set(project.images) - set(project.assets):
            raise ValueError("Project contains images missing from its asset index")
        return project

    def paths(self):
        return list(self.assets)

    def resolved_src_dir(self):
        return Path(self.extract_dir)

    def groups(self):
        return scene_groups(self.images)


def import_game(selected: str | Path, workspace: str | Path, progress=lambda message: None) -> RpgProject:
    game = detect_game(selected)
    from .enigma import has_enigma
    if any(has_enigma(p) for p in (game.root / 'Game.exe', game.root.parent / 'Game.exe')):
        raise ValueError('This game also has images packed in Game.exe. Use File > Import Packed EXE '
                         'to include them in a separate playable copy.')
    ledger_path = game.root / '.rpg-censor/deployed.json'
    if ledger_path.exists():
        import json
        ledger = json.loads(ledger_path.read_text(encoding='utf-8'))
        for name, record in ledger.items():
            if sha256(safe_path(game.root, name).read_bytes()).hexdigest() != record['original']:
                raise ValueError('Restore this game using its existing project before importing it again')
    workspace = Path(workspace).resolve()
    # Importing twice must not replace a user's review state or cached originals.
    if workspace.exists() and any(workspace.iterdir()):
        raise ValueError("Choose an empty project folder")
    if workspace.is_relative_to(game.root):
        raise ValueError("Keep the project folder outside the game's www/content folder")
    files = image_paths(game)
    if not files:
        raise ValueError("No supported images found in img/")
    originals = workspace / "originals"
    originals.mkdir(parents=True, exist_ok=True)
    project = RpgProject(game_root=str(game.root), engine=game.engine, title=game.title,
                         encryption_key=game.encryption_key, extract_dir=str(originals),
                         output_dir=str(workspace / "export"), sticker_dir=str(workspace / "stickers"),
                         archive_format="rpgmaker", project_file=workspace / "project.rgcproj.json")
    for index, file in enumerate(files):
        relative = file.relative_to(game.root).as_posix()
        safe_path(game.root, relative)
        raw = file.read_bytes()
        encrypted = raw.startswith(HEADER)
        name = str(Path(relative).with_suffix(".png")).replace("\\", "/") if encrypted else relative
        if name in project.assets:
            raise ValueError(f"Both plain and encrypted versions exist: {name}")
        decoded = decrypt_image(raw, game.encryption_key) if encrypted else raw
        try:
            decoded, repairs = validated_image(decoded)
        except Exception as error:
            raise ValueError(f'{relative}: {error}') from error
        target = safe_path(originals, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(decoded)
        project.assets[name] = {"source": relative, "encrypted": encrypted,
                                "sha256": sha256(raw).hexdigest(), "original_sha256": sha256(decoded).hexdigest(),
                                "repairs": repairs}
        if index % 50 == 0:
            progress(f"Imported {index + 1}/{len(files)} images")
    project.sync_with_paths(project.paths(), {p: scene_key(p) for p in project.assets})
    project.save()
    progress(f"Ready: {len(files)} images from {game.engine}")
    return project
