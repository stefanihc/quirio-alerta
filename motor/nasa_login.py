"""Entrada a NASA Earthdata: usa los secrets de GitHub o la clave que guardó entrar_nasa.py (_netrc)."""
import os
import sys
from pathlib import Path


def _desde_netrc():
    for nombre in ("_netrc", ".netrc"):
        f = Path.home() / nombre
        if f.exists():
            t = f.read_text(encoding="utf-8").split()
            if "login" in t and "password" in t:
                os.environ["EARTHDATA_USERNAME"] = t[t.index("login") + 1]
                os.environ["EARTHDATA_PASSWORD"] = t[t.index("password") + 1]
                return True
    return False


def entrar():
    import earthaccess
    if not (os.environ.get("EARTHDATA_USERNAME") and os.environ.get("EARTHDATA_PASSWORD")) and not _desde_netrc():
        sys.exit("No encuentro tu clave guardada. Corre primero: python entrar_nasa.py")
    try:
        auth = earthaccess.login(strategy="environment")
    except Exception:
        auth = None
    if not auth or not auth.authenticated:
        sys.exit("NASA rechazó la clave. En la PC: python entrar_nasa.py. En GitHub: revisa los secrets.")
    print("Entrada correcta a NASA Earthdata.")
