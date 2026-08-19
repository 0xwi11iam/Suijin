import base64
import binascii
import urllib.parse


def encode_text(text: str = "", scheme: str = "base64") -> str:
    """Encode text (base64, base32, hex, url, rot13)."""
    if not text:
        return "Error: text required"
    scheme = (scheme or "base64").lower()
    try:
        if scheme == "base64":
            return base64.b64encode(text.encode()).decode()
        if scheme == "base32":
            return base64.b32encode(text.encode()).decode()
        if scheme == "hex":
            return text.encode().hex()
        if scheme == "url":
            return urllib.parse.quote(text, safe="")
        if scheme == "rot13":
            import codecs

            return codecs.encode(text, "rot13")
        return f"Error: unknown scheme {scheme!r} (base64|base32|hex|url|rot13)"
    except Exception as e:
        return f"Error: {e}"


def decode_text(text: str = "", scheme: str = "base64") -> str:
    """Decode text (base64, base32, hex, url, rot13)."""
    if not text:
        return "Error: text required"
    scheme = (scheme or "base64").lower()
    try:
        if scheme == "base64":
            return base64.b64decode(text + "=" * (-len(text) % 4), validate=False).decode("utf-8", "replace")
        if scheme == "base32":
            return base64.b32decode(text + "=" * (-len(text) % 8)).decode("utf-8", "replace")
        if scheme == "hex":
            return binascii.unhexlify(text.strip()).decode("utf-8", "replace")
        if scheme == "url":
            return urllib.parse.unquote(text)
        if scheme == "rot13":
            import codecs

            return codecs.encode(text, "rot13")
        return f"Error: unknown scheme {scheme!r} (base64|base32|hex|url|rot13)"
    except Exception as e:
        return f"Error: {e}"
