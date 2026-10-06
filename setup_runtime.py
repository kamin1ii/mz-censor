"""Build-time download only. Releases contain the verified 1.8 MB launcher."""
from hashlib import sha256
from pathlib import Path
import urllib.request
from io import BytesIO
from zipfile import ZipFile

from rpg_censor.runtime import LAUNCHER_SHA256

URL = 'https://dl.nwjs.io/v0.29.0/nwjs-v0.29.0-win-ia32.zip'
ARCHIVE_SHA256 = '2f5db70c61fba63066f55a04061a095a4a09fbe245c88d920bec0230b590d1ca'


def prepare(archive=None):
    target = Path(__file__).parent / 'rpg_censor/runtime_files'
    if (target / 'nw.exe').exists() and (target / 'credits.html').exists():
        if sha256((target / 'nw.exe').read_bytes()).hexdigest() == LAUNCHER_SHA256:
            return
    if archive:
        data = Path(archive).read_bytes()
    else:
        request = urllib.request.Request(URL, headers={'User-Agent': 'Mozilla/5.0 MZ-Censor-build'})
        with urllib.request.urlopen(request, timeout=120) as response:
            data = response.read()
    if sha256(data).hexdigest() != ARCHIVE_SHA256:
        raise ValueError('Official NW.js archive checksum mismatch')
    with ZipFile(BytesIO(data)) as archive:
        launcher = archive.read('nwjs-v0.29.0-win-ia32/nw.exe')
        if sha256(launcher).hexdigest() != LAUNCHER_SHA256:
            raise ValueError('NW.js launcher checksum mismatch')
        target.mkdir(exist_ok=True)
        (target / 'nw.exe').write_bytes(launcher)
        (target / 'credits.html').write_bytes(archive.read('nwjs-v0.29.0-win-ia32/credits.html'))


if __name__ == '__main__':
    import sys
    prepare(sys.argv[1] if len(sys.argv) > 1 else None)
