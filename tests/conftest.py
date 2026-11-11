"""Test ortamı: gerçek MetaTrader5 yerine sahte modül; ağ çağrısı yok; her test temiz durumla başlar."""
import copy
import pathlib
import sys

import pytest
import yaml

KOK = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KOK))

from tests import sahte_mt5  # noqa: E402

sys.modules["MetaTrader5"] = sahte_mt5

from bot import haber, izleme, mt5_baglanti as mb, risk  # noqa: E402

with open(KOK / "config.yaml", encoding="utf-8") as _f:
    _CFG = yaml.safe_load(_f)


@pytest.fixture(autouse=True)
def temiz_durum(tmp_path, monkeypatch):
    sahte_mt5.sifirla()
    monkeypatch.setattr(risk, "DURDUR_DOSYASI", tmp_path / "DURDUR")
    mb._saat.update(ofset=0, sabit=False, son_tick=0, dosya=None)
    mb._secili.clear()
    izleme._islenen.clear()
    haber._onbellek.update(zaman=0.0, veri=[], son_deneme=0.0)
    # Hiçbir test gerçek ağa çıkmasın
    monkeypatch.setattr(haber.requests, "get", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ağ yok")))
    for ad in ("ANTHROPIC_API_KEY", "XAI_API_KEY"):
        monkeypatch.delenv(ad, raising=False)
    yield


def _temel(tmp_path):
    c = copy.deepcopy(_CFG)
    c["log_klasoru"] = str(tmp_path / "kayitlar")
    c["mod"] = "sinyal"
    c["haber"]["aktif"] = False
    return c


@pytest.fixture
def cfg(tmp_path):
    """ESKİ strateji (trend_skor) + LLM veto modu: ilk kod tabanının davranışını test eden testler için."""
    c = _temel(tmp_path)
    c["strateji"]["ad"] = "trend_skor"
    c["llm"]["mod"] = "veto"
    return c


@pytest.fixture
def cfg_gk(tmp_path):
    """Varsayılan config: gunluk_kirilim stratejisi, LLM gözlem modu."""
    return _temel(tmp_path)


@pytest.fixture
def mt5():
    return sahte_mt5
