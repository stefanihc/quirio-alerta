"""
MOTOR DE QUIRIO ALERTA — calcula solo el color del semáforo y los avisos.

Qué hace cada vez que se corre:
  1. Lluvia que YA cayó: suma GPM IMERG Early (cada 30 min) de las últimas 72 h en Quirio.
  2. Lluvia que VIENE: pronóstico ECMWF de los próximos 3 días (vía Open-Meteo, gratis, sin clave).
     Solo da un aviso de "se espera más lluvia"; NO cambia el color (ver 06_modelo/pronostico/LEEME_pronostico.md).
  3. Calcula el color (umbrales Early preliminares: 5 y 9.6 mm en 3 días) y escribe los avisos.
  4. Guarda todo en 07_aplicativo/estado.json, que es lo que lee la app.

El ROJO no sale de aquí: lo declara el COE (cámara del SAT o brigada). El motor solo propone hasta naranja.

Correr en Anaconda (entorno quirio):
    python motor_quirio.py
Más adelante esto mismo correrá solo cada 30 min en GitHub.
"""
import json
import os
import sys
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import urlopen

warnings.filterwarnings("ignore")
BASE = Path(__file__).resolve().parents[2]
SALIDA = Path(os.environ["QUIRIO_SALIDA"]) if os.environ.get("QUIRIO_SALIDA") else BASE / "07_aplicativo" / "estado.json"
LON, LAT = -76.725, -11.92
BBOX = (-76.75, -11.95, -76.70, -11.89)
AMARILLO, NARANJA = 5.0, 9.6           # mm en 3 días, GPM Early (06_modelo/umbral_lluvia/LEEME_umbral_early.md)
PRE_AVISO = 3.0                        # mm en 3 días, ECMWF (06_modelo/pronostico/LEEME_pronostico.md)
LIMA = timezone(timedelta(hours=-5))


def lluvia_observada(horas=72):
    """mm por día (hora de Lima) de IMERG Early en las últimas `horas` (llega con ~4 h de retraso)."""
    import earthaccess
    import xarray as xr
    from nasa_login import entrar
    entrar()
    fin = datetime.now(timezone.utc)
    ini = fin - timedelta(hours=horas)
    g = earthaccess.search_data(short_name="GPM_3IMERGHHE", version="07",
                                temporal=(ini.strftime("%Y-%m-%dT%H:%M"), fin.strftime("%Y-%m-%dT%H:%M")),
                                bounding_box=BBOX)
    print(f"IMERG Early: {len(g)} archivos de 30 min")
    por_dia = {}
    for f in earthaccess.open(g):
        with xr.open_dataset(f, group="Grid", engine="h5netcdf") as ds:
            mm = float(ds["precipitation"].sel(lon=LON, lat=LAT, method="nearest").values.squeeze()) * 0.5
            t = datetime.fromisoformat(str(ds["time"].values[0])[:19]).replace(tzinfo=timezone.utc).astimezone(LIMA)
        por_dia[t.date().isoformat()] = por_dia.get(t.date().isoformat(), 0.0) + mm
    return dict(sorted(por_dia.items())), len(g)


def pronostico():
    url = ("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s"
           "&daily=precipitation_sum&models=ecmwf_ifs025&timezone=America%%2FLima&forecast_days=4" % (LAT, LON))
    with urlopen(url, timeout=30) as r:
        d = json.load(r)["daily"]
    return [{"fecha": f, "mm": round(mm or 0, 1)} for f, mm in zip(d["time"], d["precipitation_sum"])][1:]


def color(p3):
    return "naranja" if p3 >= NARANJA else "amarillo" if p3 >= AMARILLO else "verde"


def avisos(dias, p3, k, pron):
    lluviosos = 0
    for mm in reversed(list(dias.values())):
        if mm < 1:
            break
        lluviosos += 1
    a = []
    if k == "naranja":
        a.append(("naranja", "Alístate para salir",
                  "La lluvia de estos días ya es alta. El cerro está cargado de agua. Ponte la mochila y ten clara tu ruta. Si el COE lo indica, sal."))
    elif k == "amarillo":
        a.append(("amarillo", "Prepárate",
                  "Está lloviendo en la parte alta de Quirio. Revisa tu mochila y ten tus documentos a la mano."))
    if lluviosos >= 2:
        a.append((k, f"{lluviosos} días seguidos de lluvia",
                  "Con varios días de lluvia el suelo se empapa y el cerro se suelta más fácil. Repasa tu plan con tu familia."))
    viene = sum(x["mm"] for x in pron)
    if viene >= PRE_AVISO:
        a.append((k, "Se espera más lluvia",
                  f"El pronóstico anuncia unos {viene:.0f} mm en los próximos 3 días. Es un pronóstico: puede fallar, pero conviene alistarse."))
    return [{"color": c, "titulo": t, "texto": x} for c, t, x in a]


if __name__ == "__main__":
    dias, n = lluvia_observada()
    p3 = round(sum(dias.values()), 1)            # todo lo que cayó en las últimas 72 h
    k = color(p3)
    try:
        pron = pronostico()
    except Exception as e:
        print("Sin pronóstico:", e)
        pron = []
    estado = {
        "actualizado": datetime.now(LIMA).isoformat(timespec="minutes"),
        "color": k, "p3_mm": p3,
        "lluvia_por_dia_mm": {f: round(v, 1) for f, v in dias.items()},
        "pronostico": pron,
        "avisos": avisos(dias, p3, k, pron),
        "fuente": f"GPM IMERG Early V07 ({n} archivos de 30 min) y Open-Meteo. Umbrales preliminares {AMARILLO} y {NARANJA} mm en 3 días.",
        "nota": "El rojo lo declara el COE de Lurigancho-Chosica. Este archivo solo propone hasta naranja.",
    }
    SALIDA.parent.mkdir(parents=True, exist_ok=True)
    SALIDA.write_text(json.dumps(estado, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nColor: {k.upper()}  |  lluvia 3 días: {p3} mm  |  pronóstico 3 días: {sum(x['mm'] for x in pron):.1f} mm")
    for a in estado["avisos"]:
        print(" -", a["titulo"])
    print("Guardado en", SALIDA)
