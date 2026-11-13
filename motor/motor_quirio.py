"""
MOTOR DE QUIRIO ALERTA — calcula solo el color del semáforo y los avisos.

Qué hace cada vez que se corre:
  1. Lluvia que YA cayó: suma GPM IMERG Early (cada 30 min) de las últimas 72 h en Quirio.
  2. Lluvia que VIENE: pronóstico ECMWF de los próximos 3 días (vía Open-Meteo, gratis, sin clave).
     Solo da un aviso de "se espera más lluvia"; NO cambia el color (ver 06_modelo/pronostico/LEEME_pronostico.md).
  3. Calcula el color (umbrales Early: 10 y 17 mm en 3 días, ver LEEME_umbral_early.md) y escribe los avisos.
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
AMARILLO, NARANJA = 10.0, 17.0         # mm en 3 días, GPM Early calibrado con 2023-2025 (06_modelo/umbral_lluvia/LEEME_umbral_early.md)
HISTERESIS = 2.0                       # mm: margen para bajar de nivel
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
    por_dia, serie = {}, []
    for f in earthaccess.open(g):
        with xr.open_dataset(f, group="Grid", engine="h5netcdf") as ds:
            mm = float(ds["precipitation"].sel(lon=LON, lat=LAT, method="nearest").values.squeeze()) * 0.5
            t = datetime.fromisoformat(str(ds["time"].values[0])[:19]).replace(tzinfo=timezone.utc).astimezone(LIMA)
        por_dia[t.date().isoformat()] = por_dia.get(t.date().isoformat(), 0.0) + mm
        serie.append((t, mm))
    lluvia_observada.serie = sorted(serie)          # cada 30 min, para la lluvia de 24 h y la intensidad
    return dict(sorted(por_dia.items())), len(g)


def pronostico():
    url = ("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s"
           "&daily=precipitation_sum&models=ecmwf_ifs025&timezone=America%%2FLima&forecast_days=4" % (LAT, LON))
    with urlopen(url, timeout=30) as r:
        d = json.load(r)["daily"]
    return [{"fecha": f, "mm": round(mm or 0, 1)} for f, mm in zip(d["time"], d["precipitation_sum"])][1:]


def tiempo_ahora():
    """Tiempo actual en Quirio (modelo de Open-Meteo, no una estación): para que la app lo muestre aunque el celular no pueda pedirlo."""
    url = ("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s"
           "&current=temperature_2m,relative_humidity_2m,cloud_cover,wind_speed_10m"
           "&daily=precipitation_probability_max&forecast_days=1&timezone=America%%2FLima" % (LAT, LON))
    with urlopen(url, timeout=30) as r:
        d = json.load(r)
    c = d["current"]
    prob = (d.get("daily") or {}).get("precipitation_probability_max") or [None]
    return {"hora": c["time"] + "-05:00", "temp_c": c["temperature_2m"], "humedad": c["relative_humidity_2m"],
            "nubes": c["cloud_cover"], "viento_kmh": c["wind_speed_10m"], "prob_lluvia_hoy": prob[0],
            "fuente": "Open-Meteo (modelo)"}


def color(p3, antes=None):
    """Color por lluvia de 3 días. Para BAJAR de nivel pide 1 mm menos que el umbral (histéresis),
    así el color no salta de ida y vuelta cada hora cuando la lluvia está justo en el límite."""
    k = "naranja" if p3 >= NARANJA else "amarillo" if p3 >= AMARILLO else "verde"
    orden = ["verde", "amarillo", "naranja"]
    if antes in orden and orden.index(k) < orden.index(antes):
        umbral_antes = NARANJA if antes == "naranja" else AMARILLO
        if p3 > umbral_antes - HISTERESIS:
            k = antes
    return k


def telegram(texto):
    """Manda el aviso al canal de Telegram si están los secrets TELEGRAM_TOKEN y TELEGRAM_CANAL."""
    token, canal = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CANAL")
    if not (token and canal):
        return
    from urllib.parse import urlencode
    from urllib.request import Request
    datos = urlencode({"chat_id": canal, "text": texto, "disable_web_page_preview": "true"}).encode()
    try:
        with urlopen(Request(f"https://api.telegram.org/bot{token}/sendMessage", data=datos), timeout=30) as r:
            print("Telegram:", r.status)
    except Exception as e:
        print("Telegram falló:", e)


def avisar_celulares(nuevos, k, p3):
    """Notificación en el celular (Web Push vía OneSignal) a todos los que activaron avisos en la app.
    Robusto: 3 intentos con espera creciente, llave anti-duplicado (si GitHub repite la corrida no llega dos veces),
    caducidad de 3 h (un aviso viejo no debe llegar tarde) y un solo aviso visible a la vez (el nuevo reemplaza al anterior).
    Nunca detiene al motor: si falla, solo lo anota en el registro."""
    # El App ID es público (también está en la app); la clave secreta solo vive en los secrets de GitHub.
    app_id = os.environ.get("ONESIGNAL_APP_ID") or "a4c2aefa-f247-4bb6-8c24-efa4c8f7efa7"
    clave = os.environ.get("ONESIGNAL_API_KEY")
    if not (app_id and clave) or not nuevos:
        return
    import hashlib
    import time
    from urllib.error import HTTPError, URLError
    from urllib.request import Request
    icono = {"verde": "🟢", "amarillo": "🟡", "naranja": "🟠"}
    principal = nuevos[0]
    titulo = f"{icono.get(principal['color'], '')} {principal['titulo']}".strip()
    texto = principal["texto"] + (f" (+{len(nuevos) - 1} aviso más)" if len(nuevos) > 1 else "")
    llave = hashlib.sha256(f"{k}|{p3}|{'|'.join(x['titulo'] for x in nuevos)}|{datetime.now(LIMA):%Y-%m-%dT%H}".encode()).hexdigest()
    llave = f"{llave[:8]}-{llave[8:12]}-{llave[12:16]}-{llave[16:20]}-{llave[20:32]}"   # formato UUID
    cuerpo = {
        "app_id": app_id, "target_channel": "push", "included_segments": ["Total Subscriptions"],
        "headings": {"es": titulo, "en": titulo}, "contents": {"es": texto, "en": texto},
        "url": "https://stefanihc.github.io/quirio-alerta/#ahora",
        "chrome_web_icon": "https://stefanihc.github.io/quirio-alerta/iconos/icono-v2-192.png",
        "ttl": 3 * 3600, "priority": 10, "web_push_topic": "quirio-estado", "idempotency_key": llave,
    }
    datos = json.dumps(cuerpo).encode()
    for intento in range(1, 4):
        try:
            req = Request("https://api.onesignal.com/notifications?c=push", data=datos, method="POST",
                          headers={"Authorization": f"Key {clave}", "Content-Type": "application/json"})
            with urlopen(req, timeout=30) as r:
                print("Avisos al celular:", r.status, r.read()[:200].decode(errors="ignore"))
                return
        except HTTPError as e:
            detalle = e.read()[:300].decode(errors="ignore")
            print(f"OneSignal respondió {e.code} (intento {intento}): {detalle}")
            if e.code < 500 and e.code != 429:
                return          # error de datos o de clave: reintentar no sirve
        except (URLError, TimeoutError) as e:
            print(f"Sin conexión con OneSignal (intento {intento}): {e}")
        time.sleep(5 * intento * intento)
    print("No se pudo enviar el aviso al celular después de 3 intentos.")


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
                  "Está lloviendo en la parte alta de Quirio. Revisa tu mochila, ten tus documentos a la mano y escucha la radio y las noticias locales."))
    if lluviosos >= 2:
        a.append((k, f"{lluviosos} días seguidos de lluvia",
                  "Con varios días de lluvia el suelo se empapa y el cerro se suelta más fácil. Repasa tu plan con tu familia."))
    viene = sum(x["mm"] for x in pron)
    if viene >= PRE_AVISO:
        a.append((k, "Se espera más lluvia",
                  f"El pronóstico anuncia unos {viene:.0f} mm en los próximos 3 días. Es un pronóstico: puede fallar, pero conviene alistarse."))
    return [{"color": c, "titulo": t, "texto": x} for c, t, x in a]


if __name__ == "__main__":
    import time
    try:
        previo = json.loads(SALIDA.read_text(encoding="utf-8"))
    except Exception:
        previo = {}
    dias = None
    for intento in range(1, 4):                   # la NASA a veces no responde: 3 intentos
        try:
            dias, n = lluvia_observada()
            if not dias:
                raise RuntimeError("la NASA no devolvió archivos de las últimas 72 h")
            break
        except (Exception, SystemExit) as e:
            print(f"Intento {intento} falló: {e}")
            dias = None
            if intento < 3:
                time.sleep(90)
    if dias is None:
        # No se inventa nada: se deja el último estado y se marca como atrasado para que la app lo diga.
        if previo:
            previo["atrasado"] = True
            previo["ultimo_intento"] = datetime.now(LIMA).isoformat(timespec="minutes")
            SALIDA.write_text(json.dumps(previo, ensure_ascii=False, indent=2), encoding="utf-8")
            print("Sin datos nuevos de la NASA: se marcó el estado como atrasado.")
            sys.exit(0)
        sys.exit("Sin datos de la NASA y sin estado anterior.")
    p3 = round(sum(dias.values()), 1)            # todo lo que cayó en las últimas 72 h
    k = color(p3, previo.get("color"))
    try:
        pron = pronostico()
    except Exception as e:
        print("Sin pronóstico:", e)
        pron = []
    serie = getattr(lluvia_observada, "serie", [])
    extra = {}
    if serie:
        ultimo = serie[-1][0]
        u24 = [(t, mm) for t, mm in serie if t > ultimo - timedelta(hours=24)]
        tmax, mmax = max(serie, key=lambda x: x[1])
        extra = {"lluvia_24h_mm": round(sum(mm for _, mm in u24), 1),
                 "intensidad_max_mm_h": round(mmax * 2, 1),          # 30 min -> mm/h
                 "hora_intensidad_max": tmax.isoformat(timespec="minutes"),
                 "dato_satelite_hasta": ultimo.isoformat(timespec="minutes")}
    estado = {
        "actualizado": datetime.now(LIMA).isoformat(timespec="minutes"),
        "color": k, "p3_mm": p3,
        "lluvia_por_dia_mm": {f: round(v, 1) for f, v in dias.items()},
        "pronostico": pron,
        "avisos": avisos(dias, p3, k, pron),
        "fuente": f"GPM IMERG Early V07 ({n} archivos de 30 min) y Open-Meteo. Umbrales preliminares {AMARILLO} y {NARANJA} mm en 3 días.",
        "nota": "El rojo lo declara el COE de Lurigancho-Chosica. Este archivo solo propone hasta naranja.",
        "umbrales_mm": {"amarillo": AMARILLO, "naranja": NARANJA},
        **extra,
    }
    try:
        estado["tiempo_ahora"] = tiempo_ahora()
    except Exception as e:
        print("Sin tiempo actual:", e)
    # ¿Qué es nuevo respecto a la corrida anterior? Solo eso se notifica (no se repite cada hora).
    titulos_antes = {x["titulo"] for x in previo.get("avisos", [])}
    nuevos = [x for x in estado["avisos"] if x["titulo"] not in titulos_antes]
    cambio = previo.get("color") and previo.get("color") != k
    estado["color_desde"] = estado["actualizado"] if (cambio or not previo) else previo.get("color_desde", estado["actualizado"])
    if cambio and k == "verde":
        nuevos.insert(0, {"color": "verde", "titulo": "Bajó la lluvia",
                          "texto": "La lluvia de los últimos 3 días bajó. Sigue atento: el suelo puede seguir mojado."})
    if nuevos:
        icono = {"verde": "🟢", "amarillo": "🟡", "naranja": "🟠"}
        telegram("\n\n".join(f"{icono.get(x['color'], '')} {x['titulo']}\n{x['texto']}" for x in nuevos)
                 + f"\n\nLluvia en 3 días: {p3} mm · Quirio Alerta\nhttps://stefanihc.github.io/quirio-alerta/")
        avisar_celulares(nuevos, k, p3)
    SALIDA.parent.mkdir(parents=True, exist_ok=True)
    SALIDA.write_text(json.dumps(estado, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nColor: {k.upper()}  |  lluvia 3 días: {p3} mm  |  pronóstico 3 días: {sum(x['mm'] for x in pron):.1f} mm")
    for a in estado["avisos"]:
        print(" -", a["titulo"])
    print("Guardado en", SALIDA)
