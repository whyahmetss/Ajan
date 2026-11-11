"""Proje klasörüne göre dosya yolları.
Bot hangi klasörden başlatılırsa başlatılsın config, .env, DURDUR ve kayitlar/ aynı yerde aranır."""
from pathlib import Path

PROJE = Path(__file__).resolve().parent.parent


def yol(p) -> Path:
    """Göreli yolu proje klasörüne göre çözer; mutlak yolu olduğu gibi döndürür."""
    p = Path(p)
    return p if p.is_absolute() else PROJE / p
