"""One way to decide that two spellings name the same place.

roster.normalize_name keeps accents, because two children may differ only
by one. Two cafés never do, and a station typed as "cafe" next to an
existing "Café" would otherwise become a second station with no
instruction linked to it.
"""

from __future__ import annotations

import unicodedata


def normalize_key(name: object) -> str:
    """'Café  Arena A', 'cafe arena a' and 'CAFÉ ARENA A' → 'cafe arena a'."""
    decomposed = unicodedata.normalize("NFKD", str(name or ""))
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(stripped.split()).casefold()
