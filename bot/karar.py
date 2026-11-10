"""3. AŞAMA - KARAR (YARGIÇ)
Önce kodla oy sayımı yapılır (ucuz). Geçerse yargıç LLM'e (Claude, Grok ya da ikisi) gider.
Yargıç yönü TERS ÇEVİREMEZ; sadece ONAY/RED ve TAM/YARIM pozisyon diyebilir.
"""
import json
import logging
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger("karar")

YARGIC_SISTEM = (
    "Sen bir trading masasının baş karar vericisisin (portföy yöneticisi). Önünde bir işlem "
    "önerisi, teknik veri ve 4 analistin oyları var. Görevin: sermayeyi korumak ve sadece "
    "yüksek kaliteli işlemleri onaylamak. Şüphede RED ver; kaçan fırsat zarar değildir. "
    "Analistlerin argümanlarını tart: Ayı analistinin geçerli bir riski varsa ciddiye al. "
    "Yönü değiştiremezsin; sadece önerilen yönü onaylar ya da reddedersin. "
    "Kurulum iyi ama belirsizlik varsa YARIM pozisyon seç. Türkçe yaz.\n\n"
    'Çıktı şeması: {"karar": "ONAY" | "RED", "guven": 0-100, "boyut": "TAM" | "YARIM", '
    '"ozet": "en fazla 2 cümle"}'
)


def oy_sayimi(oylar: list[dict], cfg) -> tuple[bool, str]:
    destek = sum(1 for o in oylar if o["ajan"] != "ayi" and o["oy"] == "DESTEK")
    ayi = next((o for o in oylar if o["ajan"] == "ayi"), None)
    if ayi and ayi["oy"] == "KARSI" and ayi["guven"] >= cfg["ayi_veto_guven"]:
        return False, f"Ayı VETO ({ayi['guven']}): {'; '.join(ayi['gerekceler'][:2])}"
    if destek < cfg["min_destek_oyu"]:
        return False, f"Yetersiz destek ({destek}/{cfg['min_destek_oyu']})"
    return True, f"{destek} destek oyu"


def _yargic(llm, saglayici: str, baglam: str, oylar: list) -> dict:
    kullanici = json.dumps({"islem": json.loads(baglam), "analist_oylari": oylar}, ensure_ascii=False)
    try:
        r = llm.sor_json(saglayici, YARGIC_SISTEM, kullanici)
        return {"model": saglayici,
                "karar": "ONAY" if str(r.get("karar", "")).upper() == "ONAY" else "RED",
                "guven": max(0, min(100, int(r.get("guven", 0)))),
                "boyut": "YARIM" if str(r.get("boyut", "")).upper() == "YARIM" else "TAM",
                "ozet": str(r.get("ozet", ""))[:400]}
    except Exception as ex:
        log.warning("Yargıç %s hata: %s", saglayici, ex)
        return {"model": saglayici, "karar": "RED", "guven": 0, "boyut": "YARIM", "ozet": f"HATA: {ex}"}


def karar_ver(llm, cfg, baglam: str, oylar: list) -> dict:
    mod = cfg["karar_modu"]
    saglayicilar = {"claude": ["claude"], "grok": ["grok"], "ikisi": ["claude", "grok"]}[mod]
    with ThreadPoolExecutor(max_workers=2) as ex:
        yargiclar = list(ex.map(lambda s: _yargic(llm, s, baglam, oylar), saglayicilar))

    esik = cfg["min_karar_guven"]
    onay = all(y["karar"] == "ONAY" and y["guven"] >= esik for y in yargiclar)
    carpan = 0.5 if any(y["boyut"] == "YARIM" for y in yargiclar) else 1.0
    return {"onay": onay, "carpan": carpan, "yargiclar": yargiclar,
            "ozet": " | ".join(f"{y['model']}: {y['karar']} ({y['guven']}) {y['ozet']}" for y in yargiclar)}
