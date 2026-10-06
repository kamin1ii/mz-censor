"""Conservative RPG Maker filename hints; never treat them as content detection."""
from pathlib import PurePosixPath
import re
from alice_censor.grouping import GroupInfo

# Bound H markers so 'fish1', 'sandwich', 'Flash', and 'Earth4' stay unflagged.
DEFAULT_FLAG_PATTERN = r"(?:^|[/_\-\s])(?:[HＨ]\d{1,3}|NSFW|R18|ERO)(?=[/_\-\s.]|$)|挿入|射精|和姦|レイプ"


def scene_key(name):
    path = PurePosixPath(name)
    stem = path.stem
    # Named pose + numeric/expression variant: 010_Luhutu01_02komaru.
    match = re.fullmatch(r'(.+\d)_(\d{1,3}[A-Za-z0-9]*)(?:_[A-Za-z0-9]+)*', stem)
    if match:
        stem = match.group(1)
    else:
        # Explicitly delimited frame numbers, while preserving scene IDs.
        stem = re.sub(r'[_-]\d{1,3}$', '', stem)
    return str(path.parent / stem)


def scene_groups(images):
    groups = {}
    for name, record in images.items():
        record.group_key = scene_key(name)
        key = record.effective_group
        groups.setdefault(key, GroupInfo(key, authoritative=False)).members.append(name)
    return groups


def flag_candidates(project, pattern=DEFAULT_FLAG_PATTERN):
    from alice_censor.project import ImageStatus
    regex = re.compile(pattern, re.IGNORECASE)
    return [name for name, rec in project.images.items()
            if rec.status == ImageStatus.UNREVIEWED and regex.search(name)]
