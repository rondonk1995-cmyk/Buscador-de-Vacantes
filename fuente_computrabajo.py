"""
fuente_computrabajo.py
=======================
Conector Computrabajo (bolsa de empleo LATAM multi-país) para el buscador
de vacantes de perfil Comercial / Pricing & COMEX (Plan A/B/C).

Computrabajo sirve HTML estático en sus listados y ofertas (sin
JavaScript necesario), por lo que se consume con urllib puro.
Países soportados vía subdominio de 2 letras.

Devuelve una lista de dicts "crudos" con el contrato:
    titulo, empresa, ubicacion, descripcion, url, is_remote_flag,
    sitio_origen, pais_busqueda
"""
import re
import html as ihtml
import time
import urllib.request

TIMEOUT_SEG = 25
PAUSA_ENTRE_BUSQUEDAS = 1.5
MAX_POR_BUSQUEDA = 8

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,*/*",
}

# Mapa cc -> nombre de país usado como pais_busqueda
PAIS_POR_CC = {
    "ar": "Argentina", "cl": "Chile", "mx": "Mexico", "co": "Colombia",
    "pe": "Peru", "uy": "Uruguay", "ec": "Ecuador", "bo": "Bolivia",
    "pa": "Panama", "ve": "Venezuela", "gt": "Guatemala",
    "do": "Dominican Republic", "hn": "Honduras", "sv": "El Salvador",
    "cr": "Costa Rica", "ni": "Nicaragua", "es": "Spain", "py": "Paraguay",
}

PAISES_COMPUTRABAJO = [
    "ar", "cl", "mx", "co", "pe", "uy", "ec", "bo",
    "cr", "es", "ve", "pa", "py",
]

# Países hispanos "remotos" (mismo nombre que PAISES_HISPANOS_REMOTO del bot)
PAIS_HISPANO = {
    "Chile", "Peru", "Mexico", "Colombia", "Uruguay", "Costa Rica",
    "Venezuela", "Ecuador", "Panama", "Paraguay", "Bolivia", "Spain",
}

# Términos de búsqueda por categoría (se slugifican para la URL).
# Basados en los términos de ROL de los perfiles Kevin (menos términos = menos
# llamadas y menor riesgo de bloqueo).
TERMINOS_POR_CATEGORIA = {
    "SINIESTROS": [
        "siniestros", "analista de siniestros", "siniestros automotor",
        "reclamos de seguros", "ajustador de siniestros",
    ],
    "SEGUROS_COMERCIAL": [
        "productor de seguros", "asesor de seguros", "ventas de seguros",
        "ejecutivo de seguros", "vendedor de seguros",
    ],
    "KAM_B2B": [
        "key account", "ejecutivo de cuentas", "ventas b2b",
        "cuentas clave", "representante comercial",
    ],
    "BACKOFFICE_ADMIN": [
        "administrativo comercial", "asistente administrativo",
        "auxiliar administrativo", "back office", "control de stock",
    ],
}


def _get(url):
    """GET simple con timeout y retorno de texto (o None)."""
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEG) as r:
            return r.read().decode("utf-8", "ignore")
    except Exception as e:
        print(f"   [!] [computrabajo] GET {url} -> {e}")
        return None


def _a_limpio(texto):
    """Texto plano sin HTML, sin acentos, minúsculas -> slug."""
    texto = ihtml.unescape(re.sub(r"<[^>]+>", " ", str(texto or "")))
    texto = texto.lower()
    texto = texto.replace("á", "a").replace("é", "e").replace("í", "i")
    texto = texto.replace("ó", "o").replace("ú", "u").replace("ñ", "n")
    texto = re.sub(r"[^a-z0-9]+", "-", texto).strip("-")
    return texto


def _extraer_listado(cc, termino):
    """Trae la lista de ofertas de un (país, término) como list[dict]."""
    slug = _a_limpio(termino)
    url = f"https://{cc}.computrabajo.com/trabajo-de-{slug}"
    html = _get(url)
    if not html:
        return []

    articulos = re.split(r'<article class="box_offer', html)[1:]
    ofertas = []

    for bloque in articulos:
        m_id = re.search(r"data-id=['\"]([^'\"]+)", bloque)
        oid = m_id.group(1) if m_id else ""

        m_link = re.search(r'href="(/ofertas-de-trabajo/[^"]+)"[^>]*>\s*([^<]+)', bloque)
        if not m_link:
            continue
        path = re.split(r"[#?]", m_link.group(1))[0]
        titulo = ihtml.unescape(m_link.group(2)).strip()
        url_oferta = f"https://{cc}.computrabajo.com{path}"
        if not (url_oferta.startswith("http") and url_oferta):
            continue

        m_emp = re.search(r'href="[^"]*\/[^"]*"[^>]*>\s*([^<]{2,60})\s*<', bloque)
        empresa = m_emp.group(1).strip() if m_emp else ""
        m_emp2 = re.search(r'href="https://[^"]+/[^"]*"[^>]*>\s*([^<]{2,60})\s*<', bloque)
        if m_emp2:
            empresa = ihtml.unescape(m_emp2.group(1)).strip()

        m_ubi = re.search(r'<p class="fs16 fc_base mt5">\s*<span[^>]*>\s*([^<]+)', bloque)
        ubicacion = ihtml.unescape(m_ubi.group(1)).strip() if m_ubi else ""

        texto_bloque = re.sub(r"<[^>]+>", " ", bloque).lower()
        modalidad = "presencial"
        if "remoto" in texto_bloque:
            modalidad = "remoto" if "presencial y remoto" not in texto_bloque and "hibrido" not in texto_bloque else "hibrido"
        if "híbrido" in texto_bloque or "hibrido" in texto_bloque or "mixto" in texto_bloque:
            modalidad = "hibrido"

        ofertas.append({
            "id": oid,
            "titulo": titulo,
            "empresa": empresa,
            "ubicacion": ubicacion,
            "modalidad": modalidad,
            "url": url_oferta,
        })

    return ofertas


_NAV_TOKENS = (
    "buscar postulaciones", "avisos favoritos", "crear cv", "ingresar",
    "menú", "login", "buscar empresas", "recruiters", "salarios",
    "consejos para encontrar", "crear alerta", "mi ubicación",
)


def _es_texto_descripcion(texto):
    """True si el párrafo parece contenido real de la oferta (no menú)."""
    if len(texto) < 100:
        return False
    bajo = texto.lower()
    return not any(t in bajo for t in _NAV_TOKENS)


def _extraer_descripcion(url_oferta):
    """Devuelve la descripción de texto de una oferta (o '' si falla)."""
    html = _get(url_oferta)
    if not html:
        return ""

    parrafos = re.findall(r"<p[^>]*>(.*?)</p>", html, re.S)
    piezas = []
    for p in parrafos:
        texto = ihtml.unescape(re.sub(r"<[^>]+>", " ", p))
        texto = re.sub(r"\s+", " ", texto).strip()
        if _es_texto_descripcion(texto):
            piezas.append(texto)

    if not piezas:
        m = re.search(r'<div[^>]*class="[^"]*(descripcion|description|descriptionJob)[^"]*"[^>]*>(.*?)</div>', html, re.S)
        if m:
            texto = ihtml.unescape(re.sub(r"<[^>]+>", " ", m.group(2)))
            texto = re.sub(r"\s+", " ", texto).strip()
            if _es_texto_descripcion(texto):
                piezas.append(texto)

    return " ".join(piezas)


def extraer_computrabajo(categoria, paises=None):
    """
    Recorre (país, término) de la categoría y devuelve crudos.
    - paises: lista de cc (ej ["ar"]). None = todos los PAISES_COMPUTRABAJO.
    - Se conserva el primer crudo por id (evita duplicados entre países).
    """
    terminos = TERMINOS_POR_CATEGORIA.get(categoria, [])
    if not terminos:
        return []

    paises = paises or PAISES_COMPUTRABAJO
    crudos = []
    vistos_ids = set()

    for cc in paises:
        if cc not in PAIS_POR_CC:
            continue
        pais_nombre = PAIS_POR_CC[cc]
        for termino in terminos:
            ofertas = _extraer_listado(cc, termino)
            print(f"   [>] [computrabajo:{cc}] '{termino}' -> {len(ofertas)} ofertas")
            procesadas = 0

            for of in ofertas:
                if not of["id"] or of["id"] in vistos_ids:
                    continue
                if procesadas >= MAX_POR_BUSQUEDA:
                    break

                vistos_ids.add(of["id"])
                procesadas += 1

                descripcion = _extraer_descripcion(of["url"])
                if len(descripcion) < 80:
                    continue  # sin descripción no se puede evaluar el rol

                crudos.append({
                    "titulo":          of["titulo"],
                    "empresa":         of["empresa"],
                    "ubicacion":       f"{of['ubicacion']} ({pais_nombre})",
                    "descripcion":     descripcion,
                    "url":             of["url"],
                    "is_remote_flag":  of["modalidad"] == "remoto",
                    "sitio_origen":    f"computrabajo:{cc}",
                    "pais_busqueda":   pais_nombre,
                })
                time.sleep(PAUSA_ENTRE_BUSQUEDAS)

        time.sleep(PAUSA_ENTRE_BUSQUEDAS)

    return crudos


if __name__ == "__main__":
    import sys
    categoria = sys.argv[1] if len(sys.argv) > 1 else "SINIESTROS"
    paises = sys.argv[2:] or ["ar"]
    print(f"Probando Computrabajo para '{categoria}' ({paises})...")
    resultados = extraer_computrabajo(categoria, paises=paises)
    print(f"Total extraído: {len(resultados)}")
    for r in resultados[:5]:
        print(" -", r["titulo"], "|", r["empresa"], "|", r["pais_busqueda"])
