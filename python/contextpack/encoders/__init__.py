"""Encoders package for ContextPack cards."""
from enum import Enum


class Encoding(str, Enum):
    yaml = "yaml"
    json = "json"
    toon = "toon"
    text = "text"


def get_encoder(encoding: str | Encoding):
    """Return the appropriate encoder for the given encoding type."""
    if isinstance(encoding, Encoding):
        enc_str = encoding.value
    else:
        enc_str = str(encoding).lower()

    if enc_str == "yaml":
        from .yaml_encoder import YamlEncoder
        return YamlEncoder()
    elif enc_str == "json":
        from .json_encoder import JsonEncoder
        return JsonEncoder()
    elif enc_str == "toon":
        from .toon_encoder import ToonEncoder
        return ToonEncoder()
    elif enc_str == "text":
        from .text_encoder import TextEncoder
        return TextEncoder()
    else:
        raise ValueError(f"Unknown encoding: {encoding}")


__all__ = ["Encoding", "get_encoder"]
