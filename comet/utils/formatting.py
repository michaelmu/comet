import base64
import math

from comet.core.sources import MAX_SIGNED_BIGINT

_SIZE_MULTIPLIERS = {
    "b": 1,
    "kb": 1024,
    "mb": 1024**2,
    "gb": 1024**3,
    "tb": 1024**4,
}


def normalize_info_hash(info_hash: str) -> str:
    if len(info_hash) == 32:
        try:
            info_hash = base64.b16encode(base64.b32decode(info_hash.upper())).decode(
                "utf-8"
            )
        except ValueError:
            pass

    if len(info_hash) == 80:
        try:
            decoded_bytes = bytes.fromhex(info_hash)
            decoded_str = decoded_bytes.decode("ascii")
            if len(decoded_str) == 40:
                int(decoded_str, 16)  # Validate it's hex
                info_hash = decoded_str
        except ValueError:
            pass

    return info_hash.lower()


def size_to_bytes(size_str: str):
    if type(size_str) is not str:
        return None
    parts = size_str.split()
    if len(parts) != 2:
        return None
    value, unit = parts

    try:
        value = float(value)
    except ValueError:
        return None
    unit = unit.lower()

    multiplier = _SIZE_MULTIPLIERS.get(unit)
    if multiplier is None or not math.isfinite(value) or value < 0:
        return None

    size_bytes = value * multiplier
    if not math.isfinite(size_bytes) or size_bytes > MAX_SIGNED_BIGINT:
        return None
    return int(size_bytes)


def format_chilllink(components: dict, cached: bool):
    metadata = ["⚡ Instant" if cached else "⬇️ Not Cached"]

    for key, value in components.items():
        if key != "title":
            metadata.append(value)

    return metadata
