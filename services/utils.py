import re, hashlib

def strip_tags(text):
    if not text: return ""
    return re.sub('<[^<]+?>', '', str(text))

def generate_uid(source, symbol, headline, date_str):
    """Generates a stable, unique ID for an announcement."""
    raw = f"{source}{symbol}{headline}{date_str}".strip().lower()
    return hashlib.md5(raw.encode()).hexdigest()
