"""Extract and account for every file in an Enigma Virtual Box container."""
import contextlib
from hashlib import file_digest
import mmap
import os
from pathlib import Path

from .engine import safe_path


def file_sha256(path):
    # Packed snapshots can be several gigabytes; never load one just to hash it.
    with path.open('rb') as stream:
        return file_digest(stream, 'sha256').hexdigest()


def has_enigma(path):
    from evbunpack import __main__ as evb
    if not path.is_file() or path.stat().st_size == 0:
        return False
    with path.open('rb') as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as mapped:
        return mapped.find(evb.EVB_MAGIC) >= 0


def extract_exe(source, destination, progress=lambda message: None):
    from evbunpack import __main__ as evb
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('The playable-copy folder must be empty')
    destination.mkdir(parents=True, exist_ok=True)
    rows, seen = [], set()
    with source.open('rb') as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as mapped:
        offset = mapped.find(evb.EVB_MAGIC)
        if offset < 0:
            raise ValueError('This executable is not a supported Enigma Virtual Box package')
        stream.seek(offset)
        nodes = iter(list(evb.pe_external_tree(stream)))
        main = next(nodes)

        def visit(node, parent):
            name = node.get('name', '')
            if name == '%DEFAULT FOLDER%':
                target = parent
            else:
                if not name or name in ('.', '..') or any(c in name for c in '\\/:'):
                    raise ValueError('Invalid name in executable package')
                target = safe_path(destination, (parent / name).relative_to(destination).as_posix())
            if node['type'] == evb.NODE_TYPE_FOLDER:
                target.mkdir(parents=True, exist_ok=True)
                for _ in range(node['objects_count']):
                    visit(next(nodes), target)
            elif node['type'] == evb.NODE_TYPE_FILE:
                relative = target.relative_to(destination).as_posix()
                if relative.casefold() in seen:
                    raise ValueError(f'Duplicate packed file: {relative}')
                seen.add(relative.casefold())
                progress(f'Unpacking {relative}')
                with open(os.devnull, 'w') as quiet, contextlib.redirect_stderr(quiet):
                    evb.process_file_node(stream, str(target), node)
                if target.stat().st_size != node['original_size']:
                    raise ValueError(f'Incomplete extraction: {relative}')
                rows.append({'path': relative, 'size': target.stat().st_size,
                             'sha256': file_sha256(target)})
            else:
                raise ValueError('Unsupported packed entry type')

        for _ in range(main['objects_count']):
            visit(next(nodes), destination)
        if next(nodes, None) is not None:
            raise ValueError('Unaccounted entries remain in the packed file table')
    return rows
