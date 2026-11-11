"""3. AŞAMA - KARAR (YARGIÇ)
Önce kodla oy sayımı yapılır (ucuz). Geçerse yargıç LLM'e (Claude, Grok ya da ikisi) gider.
Yargıç sadece ONAY / RED der: yönü değiştiremez, lotu değiştiremez (lot tamamen kodda).
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
    "Yönü değiştiremezsin; sadece önerilen yönü onaylar ya da reddedersin. Pozisyon "
    "büyüklüğü sabittir ve kod tarafından belirlenir. Türkçe yaz.\n\n"
    'Çıktı şeması: {"karar": "ONAY" | "RED", "guven": 0-100, "ozet": "en fazla 2 cümle"}'
)


def oy_sayimi(oylar: list[dict], cfg) -> tuple[bool, str]:
    ayi = next((o for o in oylar if o["ajan"] == "ayi"), None)
    if ayi is None or ayi["oy"] == "HATA":
        return False, "Ayı ajanı geçerli cevap vermedi: veto kontrol edilemedi (güvenli taraf: RED)"
    if ayi["oy"] == "KARSI" and ayi["guven"] >= cfg["ayi_veto_guven"]:
        return False, f"Ayı VETO ({ayi['guven']}): {'; '.join(ayi['gerekceler'][:2])}"
    destek = sum(1 for o in oylar if o["ajan"] != "ayi" and o["oy"] == "DESTEK")
    if destek < cfg["min_destek_oyu"]:
        return False, f"Yetersiz destek ({destek}/{cfg['min_destek_oyu']})"
    return True, f"{destek} destek oyu"


def _yargic(llm, saglayici: str, baglam: str, oylar: list) -> dict:
    kullanici = json.dumps({"islem": json.loads(baglam), "analist_oylari": oylar}, ensure_ascii=False)
    try:
        r = llm.sor_json(saglayici, YARGIC_SISTEM, kullanici, rol="yargic")
        karar = str(r.get("karar", "")).strip().upper()
        try:
            guven = int(round(float(str(r.get("guven", 0)).strip().rstrip("%"))))
        except (TypeError, ValueError):
            guven = 0
        return {"model": saglayici, "karar": "ONAY" if karar == "ONAY" else "RED",
                "guven": max(0, min(100, guven)), "ozet": str(r.get("ozet", ""))[:400]}
    except Exception as ex:
        log.warning("Yargıç %s hata: %s", saglayici, ex)
        return {"model": saglayici, "karar": "RED", "guven": 0, "ozet": f"HATA: {str(ex)[:300]}"}


def karar_ver(llm, cfg, baglam: str, oylar: list) -> dict:
    mod = cfg["karar_modu"]
    saglayicilar = {"claude": ["claude"], "grok": ["grok"], "ikisi": ["claude", "grok"]}[mod]
    with ThreadPoolExecutor(max_workers=2) as ex:
        yargiclar = list(ex.map(lambda s: _yargic(llm, s, baglam, oylar), saglayicilar))
    esik = cfg["min_karar_guven"]
    onay = all(y["karar"] == "ONAY" and y["guven"] >= esik for y in yargiclar)
    return {"onay": onay, "yargiclar": yargiclar,
            "ozet": " | ".join(f"{y['model']}: {y['karar']} ({y['guven']}) {y['ozet']}" for y in yargiclar)}
