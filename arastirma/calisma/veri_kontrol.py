"""Veri kalite kontrolü: mum sayısı, boşluklar, spread/ATR oranı, aykırı değerler."""
import json
import numpy as np, pandas as pd
from bot.yollar import yol
from bot import indikator
meta = json.loads((yol("veri") / "semboller.json").read_text(encoding="utf-8"))
satir = []
for s in meta["semboller"]:
    d = pd.read_csv(yol("veri") / s["dosyalar"]["H1"])
    t = d["time"].to_numpy(); pt = s["bilgi"]["point"]
    atr = indikator.atr(d).to_numpy()
    ard = (np.diff(t) == 3600).mean()
    ret = np.abs(np.diff(np.log(d["close"].to_numpy())))
    sp = d["spread"].to_numpy() * pt
    satir.append({"sembol": s["ad"], "sinif": s["sinif"], "ilk": s["ilk"], "son": s["son"], "mum": len(d),
                  "ardisik_%": round(100 * ard, 1), "spread/ATR_medyan": round(float(np.nanmedian(sp[100:] / atr[100:])), 3),
                  "spread/ATR_p95": round(float(np.nanpercentile(sp[100:] / atr[100:], 95)), 3),
                  "ATR/fiyat_%": round(float(100 * np.nanmedian(atr[100:] / d["close"].to_numpy()[100:])), 3),
                  "max_1mum_%": round(float(100 * ret.max()), 1), "h<l": int((d.high < d.low).sum()),
                  "kapanis_disi": int(((d.close > d.high) | (d.close < d.low)).sum())})
print(pd.DataFrame(satir).to_string(index=False))
