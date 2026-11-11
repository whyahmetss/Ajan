"""İzleme: mum kapanışında başa baş / takip eden stop; stop ASLA geri gitmez; her mum bir kez."""
import numpy as np
import pytest

from bot import izleme
from tests.yardimci import PAZARTESI, elle_mumlar

H = 3600
SP = 10                      # mum spread'i (point)


def _kur(mt5, kapanislar, al=True, giris_index=60):
    """60 düz mum (ATR ısınması) + verilen kapanışlar. Pozisyon giris_index mumunun açılışında."""
    satir = [(1.1000, 1.1002, 1.0998, 1.1000)] * giris_index
    onceki = 1.1000
    for c in kapanislar:
        satir.append((onceki, max(onceki, c) + 0.0001, min(onceki, c) - 0.0001, c))
        onceki = c
    satir.append((onceki, onceki, onceki, onceki))               # oluşmakta olan mum
    mumlar = elle_mumlar(satir, spread_pt=SP)
    mt5.sembol_ekle("EURUSD")
    mt5.mum_ayarla("EURUSD", mt5.TIMEFRAME_H1, mumlar)
    giris_t = PAZARTESI + giris_index * H + 15
    if al:
        p = mt5.pozisyon_ekle(symbol="EURUSD", time=giris_t, price_open=1.1001, sl=1.0986, tp=1.1031)
    else:
        p = mt5.pozisyon_ekle(symbol="EURUSD", type=mt5.POSITION_TYPE_SELL, time=giris_t,
                              price_open=1.1000, sl=1.1015, tp=1.0970)
    return p, mumlar


def _yonet(cfg, mt5, mumlar, kac_mum_sonra, fiyat=None):
    """Tick'i, giriş mumundan 'kac_mum_sonra' mum sonraya koy ve yönet."""
    t = int(mumlar["time"][60]) + kac_mum_sonra * H + 20
    c = fiyat if fiyat is not None else float(mumlar["close"][60 + kac_mum_sonra - 1])
    mt5.tick_ayarla("EURUSD", c, c + 0.0001, t)
    izleme.pozisyonlari_yonet(cfg)


def test_giris_mumu_kapanmadan_dokunulmaz(cfg, mt5):
    p, m = _kur(mt5, [1.1030])
    mt5.tick_ayarla("EURUSD", 1.1030, 1.1031, int(m["time"][60]) + 1800)   # giriş mumunun içi
    izleme.pozisyonlari_yonet(cfg)
    assert mt5.D.gonderilen == [] and p.sl == 1.0986


def test_basa_bas_mum_kapanisinda(cfg, mt5):
    p, m = _kur(mt5, [1.1017])                    # kapanışta kâr 0.0016 >= 1R (0.0015)
    _yonet(cfg, mt5, m, 1)
    assert p.sl == pytest.approx(1.1001 + SP * 1e-5 + 2e-5)
    assert len(mt5.D.gonderilen) == 1
    _yonet(cfg, mt5, m, 1)                        # aynı mum tekrar işlenmez
    assert len(mt5.D.gonderilen) == 1


def test_mum_ici_tepe_tetiklemez(cfg, mt5):
    """O1/S4: mum içinde 1R'yi görüp kapanışta altında kalan fiyat başa baş tetiklemez (backtest ile aynı)."""
    p, m = _kur(mt5, [1.1010])
    m["high"][60] = 1.1040
    _yonet(cfg, mt5, m, 1, fiyat=1.1030)          # şu anki fiyat yüksek olsa bile
    assert mt5.D.gonderilen == [] and p.sl == 1.0986


def test_takip_eden_stop_geri_gitmez(cfg, mt5):
    p, m = _kur(mt5, [1.1017, 1.1025, 1.1019, 1.1022, 1.1012])
    sl_gecmisi = []
    for k in range(1, 6):
        _yonet(cfg, mt5, m, k)
        sl_gecmisi.append(p.sl)
    assert sl_gecmisi[1] > sl_gecmisi[0] > 1.1001          # BE sonra trailing
    assert all(b >= a for a, b in zip(sl_gecmisi, sl_gecmisi[1:]))
    assert sl_gecmisi[-1] == sl_gecmisi[1]                  # düşüşte stop geri çekilmedi


def test_satis_simetrik(cfg, mt5):
    p, m = _kur(mt5, [1.0983, 1.0975, 1.0990], al=False)
    sl = []
    for k in range(1, 4):
        _yonet(cfg, mt5, m, k)
        sl.append(p.sl)
    assert sl[0] == pytest.approx(1.1000 - SP * 1e-5 - 2e-5)
    assert sl[1] < sl[0] and sl[2] == sl[1]


def test_rastgele_yollarda_stop_monoton(cfg, mt5):
    rng = np.random.default_rng(5)
    for tohum in range(15):
        mt5.sifirla()
        izleme._islenen.clear()
        yol = list(1.1001 + np.cumsum(rng.normal(0.0002, 0.0008, 25)))
        p, m = _kur(mt5, yol)
        onceki = p.sl
        for k in range(1, 26):
            if not mt5.D.pozisyonlar:
                break
            _yonet(cfg, mt5, m, k)
            assert p.sl >= onceki - 1e-12, (tohum, k)
            onceki = p.sl


def test_gercek_hesapta_stop_degismez(cfg, mt5):
    p, m = _kur(mt5, [1.1017])
    mt5.D.hesap.trade_mode = mt5.ACCOUNT_TRADE_MODE_REAL
    _yonet(cfg, mt5, m, 1)
    assert p.sl == 1.0986 and mt5.D.gonderilen == []


def test_broker_min_mesafesi(cfg, mt5):
    p, m = _kur(mt5, [1.1017])
    mt5.D.semboller["EURUSD"].trade_stops_level = 500        # fiyata 500 point'ten yakın stop yok
    _yonet(cfg, mt5, m, 1)
    assert p.sl == 1.0986
