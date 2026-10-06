"""An exact-match clean NW.js launcher for Enigma's wrapped-host layout."""
from hashlib import sha256
from pathlib import Path
import shutil

LAUNCHER_SHA256 = 'e9fd4a3d4e923420314b6ef5c7eb2347049f5cf8251efc89bb746e88b43c961b'
RUNTIME_HASHES = {
    'nw.dll': '4a3f39e9476288d109dbfe0b0c245f032ebf920c5142af36a2a15b31f9ff2139',
    'node.dll': '8d31c14a59cccb093ad1264c43e4d032a9cfcefeaa0d45b6862a5776c44fff37',
    'natives_blob.bin': '47a86495fbc403ade14d7e451c33edb1baa810402d8d192446a7767bc563dd47',
    'snapshot_blob.bin': '4df77f0eb8a8b842a87ba8e7377a86054b444d9ef59ec6c7d111803e3ed61030',
}


def install_launcher(playable: Path):
    for name, expected in RUNTIME_HASHES.items():
        file = playable / name
        if not file.is_file() or sha256(file.read_bytes()).hexdigest() != expected:
            raise ValueError('This wrapped EXE needs a different NW.js runtime. '
                             'The bundled clean launcher supports NW.js 0.29.0 Windows ia32 only.')
    resources = Path(__file__).with_name('runtime_files')
    launcher = resources / 'nw.exe'
    if not launcher.exists():
        raise ValueError('Clean launcher resources are missing. Source users: run python setup_runtime.py.')
    if sha256(launcher.read_bytes()).hexdigest() != LAUNCHER_SHA256:
        raise ValueError('Clean launcher checksum mismatch')
    shutil.copyfile(launcher, playable / 'Game.exe')
    shutil.copyfile(resources / 'credits.html', playable / 'nwjs-credits.html')
