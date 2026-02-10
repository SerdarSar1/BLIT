# shared_protocol.py
import json

def encode(obj: dict) -> bytes:
    """Convert a Python dict to minified JSON bytes (UTF-8)."""
    return json.dumps(obj, separators=(",", ":")).encode("utf-8")

def decode(data: bytes) -> dict:
    """Convert JSON bytes back into a dict; return {} on failure."""
    try:
        return json.loads(data.decode("utf-8"))
    except Exception:
        return {}
