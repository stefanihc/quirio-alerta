"""
Convierte la ortofoto del dron (GeoTIFF en EPSG:3857) en teselas XYZ para el mapa de Quirio Alerta.
Uso:  python herramientas/teselas_orto.py orto_fuente/orto_quirio_app.tif orto
- Zooms 14 a 19 (en z19 cada píxel mide ~0,30 m, igual que la ortofoto).
- El borde blanco sin datos se vuelve transparente (solo el blanco conectado al borde, no los techos blancos).
- Formato WebP con transparencia; no se guardan teselas vacías.
"""
import math
import os
import sys

import numpy as np
import rasterio
from PIL import Image
from rasterio.enums import Resampling
from rasterio.windows import from_bounds
from scipy import ndimage

SRC, OUT = sys.argv[1], sys.argv[2]
ZMIN, ZMAX = 14, 19
R = 20037508.342789244          # mitad del mundo en EPSG:3857
T = 256

src = rasterio.open(SRC)
assert src.crs.to_epsg() == 3857, "La ortofoto debe estar en EPSG:3857"
b = src.bounds

# Máscara de "sin datos": blanco/negro casi puro CONECTADO al borde, calculada a 1/4 de resolución.
f = 4
small = src.read(out_shape=(3, src.height // f, src.width // f), resampling=Resampling.nearest).astype(np.int16)
vacio = ((small.min(0) >= 238) & (small.max(0) - small.min(0) <= 14)) | (small.max(0) <= 8)
vacio = ndimage.binary_opening(vacio, iterations=1)
vacio[:2, :] = vacio[-2:, :] = True   # el borde del archivo trae una línea gris de 1 px
vacio[:, :2] = vacio[:, -2:] = True
lab, n = ndimage.label(vacio)
borde = np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))
nodata_small = np.isin(lab, borde[borde > 0])
nodata_small = ndimage.binary_dilation(nodata_small, iterations=2)
print("Sin datos en el borde: %.1f %%" % (100 * nodata_small.mean()))


def tile_bounds(z, x, y):
    s = 2 * R / 2 ** z
    return (-R + x * s, R - (y + 1) * s, -R + (x + 1) * s, R - y * s)


def rango(z):
    s = 2 * R / 2 ** z
    x0, x1 = int((b.left + R) // s), int((b.right + R) // s)
    y0, y1 = int((R - b.top) // s), int((R - b.bottom) // s)
    return range(x0, x1 + 1), range(y0, y1 + 1)


total = peso = 0
for z in range(ZMIN, ZMAX + 1):
    xs, ys = rango(z)
    for x in xs:
        for y in ys:
            tb = tile_bounds(z, x, y)
            w = from_bounds(*tb, transform=src.transform)
            rgb = src.read(window=w, out_shape=(3, T, T), boundless=True, fill_value=255,
                           resampling=Resampling.average if z < ZMAX else Resampling.bilinear)
            # máscara para esta tesela (desde la máscara reducida)
            r0, c0 = w.row_off / f, w.col_off / f
            rr = (r0 + (np.arange(T) + 0.5) * w.height / f / T).astype(int)
            cc = (c0 + (np.arange(T) + 0.5) * w.width / f / T).astype(int)
            fuera = (rr[:, None] < 0) | (rr[:, None] >= nodata_small.shape[0]) | (cc[None, :] < 0) | (cc[None, :] >= nodata_small.shape[1])
            m = np.ones((T, T), bool)
            ok = ~fuera
            m[ok] = nodata_small[np.clip(rr, 0, nodata_small.shape[0] - 1)[:, None].repeat(T, 1)[ok],
                                 np.clip(cc, 0, nodata_small.shape[1] - 1)[None, :].repeat(T, 0)[ok]]
            if m.all():
                continue
            alpha = np.where(m, 0, 255).astype(np.uint8)
            img = Image.fromarray(np.dstack([np.transpose(rgb, (1, 2, 0)), alpha]), "RGBA")
            os.makedirs(f"{OUT}/{z}/{x}", exist_ok=True)
            p = f"{OUT}/{z}/{x}/{y}.webp"
            img.save(p, "WEBP", quality=72, method=4)
            total += 1
            peso += os.path.getsize(p)
    print(f"z{z}: listo")
print(f"{total} teselas, {peso/1e6:.1f} MB")
