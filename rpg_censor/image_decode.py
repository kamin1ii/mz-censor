"""Validate pixels while tolerating one known, non-pixel PNG metadata defect."""
from io import BytesIO
import struct
import zlib

from PIL import Image

from .engine import PNG


def validated_image(data: bytes) -> tuple[bytes, list[str]]:
    """Drop bad-CRC ICC profiles only; never repair damaged image chunks.

    Chromium accepts these files but Pillow rejects them. Keep their original
    encrypted bytes for Restore, and record the cache-only metadata repair.
    """
    repairs = []
    if data.startswith(PNG):
        chunks, offset = [PNG], len(PNG)
        while offset < len(data):
            if offset + 12 > len(data):
                raise ValueError('Truncated PNG chunk')
            size = struct.unpack_from('>I', data, offset)[0]
            end = offset + 12 + size
            if end > len(data):
                raise ValueError('Truncated PNG chunk payload')
            kind = data[offset + 4:offset + 8]
            crc = zlib.crc32(data[offset + 4:end - 4]) & 0xffffffff
            if crc != struct.unpack_from('>I', data, end - 4)[0]:
                if kind != b'iCCP':
                    raise ValueError(f'Invalid PNG checksum: {kind!r}')
                repairs.append('Removed iCCP color profile with invalid checksum; pixel data unchanged')
            else:
                chunks.append(data[offset:end])
            offset = end
            if kind == b'IEND':
                chunks.append(data[offset:])
                break
        if repairs:
            data = b''.join(chunks)
    with Image.open(BytesIO(data)) as image:
        if getattr(image, 'n_frames', 1) > 1:
            raise ValueError('Animated image requires manual review (not silently skipped)')
        image.verify()
    # verify() checks structure; load() also proves the compressed pixels decode.
    with Image.open(BytesIO(data)) as image:
        image.load()
    return data, repairs
