"""
BUSCADOR DE VACANTES — Kevin Rondón · Plan A / B / C / D (multi-fuente)
=======================================================================
Versión 12: Perfil afinado a partir del CV de Kevin Rondón
  Analista de Siniestros | Ejecutivo Comercial B2B & B2C | Gestión de Seguros
  Formación: TSU en Riesgos y Seguros (IUS) · Curso de Seguros Internacionales
  Experiencia: Jalles Broker (siniestros automotor/ICO/TRO/RC/ART), Nelson
  Jellinek (siniestros patrimoniales y salud), Hipotecario Seguros (venta de
  seguros vida/salud), Unibell (comercial B2C/B2B, e-commerce, pricing, CRM),
  Guaticobre (comercial + back office + inventario).

  Plan A = Analista de Siniestros / Reclamos de Seguros (foco principal)
  Plan B = Ejecutivo / Asesor Comercial de Seguros (PAS / Productor)
  Plan C = Ejecutivo de Cuentas / Key Account B2B (fuera de seguros)
  Plan D = Analista Administrativo / Back Office Comercial (puerta de entrada)

FUENTES (v12):
  - LinkedIn + Indeed vía JobSpy (primary).
  - Computrabajo (LATAM multi-país, HTTP puro).
  - Bumeran + ZonaJobs (SPA con Playwright chromium).
  Todas devuelven "crudos" con el mismo contrato y se evalúan con la misma
  lógica de filtrado (fuente_computrabajo.py / fuente_spa.py).

  MODO ARGENTINA — FILTRO GEOGRÁFICO:
  Solo se envían vacantes ubicadas en CABA, Gran Buenos Aires o Provincia de
  Buenos Aires. Se descartan Rosario, Santa Fe, Mendoza, Bariloche, Córdoba,
  etc. Las vacantes remotas o sin ubicación especificada NO se filtran.

MEJORAS:
  - Matching ponderado por "rol" (título pesa 4x / descripción 2x) y "contexto"
    (título 2x / descripción 1x), con puntaje mínimo por categoría.
  - Se exige al menos 1 término de ROL: evita falsos positivos donde solo
    coincide una skill (p.ej. "excel") pero el puesto no es del perfil.
  - "Contexto exigido" por categoría (p.ej. Plan B exige "seguro", Plan C exige
    contexto B2B/empresas): mantiene precisión en roles genéricos.
  - Bloqueo de puestos fuera del perfil: hostelería, venta minorista
    presencial, telemarketing/call center, trabajo manual/depósito, IT/consultor,
    contador/auditor.
  - Matching insensible a acentos (Siniestros, Seguros, Facturación, etc.).

v12 — FLUJOS SEPARADOS:
  - Modo ARGENTINA (ejecución 4x/día): busca SOLO en Argentina (planes A/B/C/D).
  - Modo PAÍSES (ejecución 2x/semana, lunes y viernes): solo remoto, plan C
    (Key Account B2B internacional). Seguros y siniestros son mercados locales.
  Ambos modos comparten la misma lógica de filtrado y el mismo archivo de
  "ya vistos" (no se repiten vacantes entre flujos).

REQUISITOS:
    pip install python-jobspy langdetect pandas schedule python-dotenv playwright
    python -m playwright install chromium   (para Bumeran/ZonaJobs)

CONFIGURACIÓN GMAIL (una vez):
    1. Activar verificación en 2 pasos en tu cuenta Gmail.
    2. Generar una "Contraseña de aplicación":
       https://myaccount.google.com/apppasswords
    3. Completar EMAIL_REMITENTE, EMAIL_DESTINATARIO y
       EMAIL_APP_PASSWORD en un archivo .env (o variables de entorno).

EJECUCIÓN (una vez y termina):
    - Argentina (4x/día en GitHub Actions):
        python alerta_empleos_linkedin.py --once --solo-argentina
    - Otros países, solo remoto (2x/semana en GitHub Actions):
        python alerta_empleos_linkedin.py --once --solo-paises
    - Ambos (todas las búsquedas, sin separar):
        python alerta_empleos_linkedin.py --once

MODO PROGRAMADO (local, opcional):
    python alerta_empleos_linkedin.py [--solo-argentina|--solo-paises]
"""

import sys
import json
import os
import time
import smtplib
import unicodedata
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import schedule
import pandas as pd
from jobspy import scrape_jobs
from langdetect import detect, LangDetectException
from dotenv import load_dotenv
load_dotenv()

pd.set_option('display.max_colwidth', None)
pd.set_option('display.width', None)

# ─────────────────────────────────────────────
#  CONFIG EMAIL (Gmail)
# ─────────────────────────────────────────────
EMAIL_APP_PASSWORD = os.getenv("EMAIL_APP_PASSWORD")
EMAIL_REMITENTE = os.getenv("EMAIL_REMITENTE")
EMAIL_DESTINATARIO = os.getenv("EMAIL_DESTINATARIO")
EMAIL_ASUNTO            = "Vacantes nuevas Kevin - Planes A/B/C/D"
EMAIL_ASUNTO_RECHAZADAS = "Vacantes rechazadas Kevin (casi califican)"

# ─────────────────────────────────────────────
#  CONFIG GENERAL
# ─────────────────────────────────────────────
CANTIDAD_POR_BUSQUEDA = 25
HORAS_ANTIGUEDAD      = 48
DEBUG                 = True

ARCHIVO_VISTOS       = "vacantes_vistas_Kevin.json"
HORARIOS_EJECUCION   = ["09:00", "12:00", "16:00", "20:00"]   # modo programado local (4x/día)

# ─────────────────────────────────────────────
#  MODOS DE BÚSQUEDA (flujos separados)
#  - MODO_ARGENTINA: solo Argentina              → 4x/día en GitHub Actions
#  - MODO_PAISES:    demás países hispanos remoto → 2x/semana (lun y vie)
#  - MODO_COMPLETO:  Argentina + otros países     → (para uso manual)
# ─────────────────────────────────────────────
MODO_ARGENTINA = "argentina"
MODO_PAISES    = "paises"
MODO_COMPLETO  = "completo"
MODO_ACTUAL    = MODO_COMPLETO   # se ajusta desde sys.argv en main()



# ─────────────────────────────────────────────
#  NORMALIZACIÓN (minúsculas + sin acentos)
# ─────────────────────────────────────────────

def normalizar(texto):
    """Convierte a minúsculas y quita acentos para matching robusto."""
    texto = str(texto or "").lower()
    texto = unicodedata.normalize('NFKD', texto)
    return ''.join(c for c in texto if not unicodedata.combining(c))


# ─────────────────────────────────────────────
#  PERFILES → PLAN (basados en el CV de Kevin Rondón)
#  Plan A = Analista de Siniestros / Reclamos de Seguros (foco principal)
#  Plan B = Ejecutivo / Asesor Comercial de Seguros (PAS / Productor)
#  Plan C = Ejecutivo de Cuentas / Key Account B2B (fuera de seguros)
#  Plan D = Analista Administrativo / Back Office Comercial
# ─────────────────────────────────────────────

# Los términos se definen NORMALIZADOS (sin acentos). El matching usa
# normalizar() sobre el texto de la vacante.
PERFILES = {

    # ── PLAN A ── Analista de Siniestros (perfil principal de Kevin)
    # 4+ años gestionando siniestros patrimoniales, automotor, salud y ART
    # en brokers y aseguradoras (Jalles Broker, Nelson Jellinek). Es el
    # camino de menor fricción para conseguir entrevistas rápido.
    "SINIESTROS": {
        "plan":             "A",
        "etiqueta":         "Analista de Siniestros",
        "min_score":        4,
        "rol_en_titulo":    True,
        "contexto_exigido": [],
        "roles": [
            "siniestros", "siniestros patrimoniales", "siniestros automotor",
            "siniestros automotores", "siniestros de salud", "siniestros de art",
            "siniestros de riesgos", "siniestros de rc", "siniestros tro",
            "analista de siniestros", "asistente de siniestros",
            "asesor de siniestros", "gestor de siniestros",
            "gestion de siniestros", "coordinador de siniestros",
            "administrativo de siniestros", "atencion de siniestros",
            "recepcion de siniestros", "tramite de siniestros",
            "liquidador de siniestros", "liquidacion de siniestros",
            "ajustador de siniestros", "ajustador de reclamos",
            "inspector de siniestros", "especialista en siniestros",
            "analista de reclamos", "analista de reclamos y siniestros",
            "analista de reclamos de seguros", "especialista en reclamos",
            "reclamos de seguros", "siniestros y reclamos",
            "analista de riesgos del trabajo", "analista de art",
            "analista de siniestros y ventas",
        ],
        "contexto": [
            "aseguradora", "compania aseguradora", "compania de seguros",
            "broker de seguros", "productora de seguros", "poliza", "polizas",
            "endoso", "suma asegurada", "deducible", "reclamo", "reclamos",
            "siniestralidad", "responsabilidad civil", "tro",
            "toda riesgo", "mercaderia", "transporte", "automotor", "salud",
            "riesgos del trabajo", "art", "ilt", "ilig", "reintegro",
            "perito", "peritaje", "taller", "liquidacion", "cobertura",
            "coberturas", "coseguro", "franquicia", "rc",
        ],
    },

    # ── PLAN B ── Ejecutivo / Asesor Comercial de Seguros (PAS / Productor)
    # Formación en Riesgos y Seguros + ventas B2B/B2C. En Argentina puede
    # requerir matrícula de Productor Asesor de Seguros (PAS). Se exige
    # contexto "seguro/s" en rol o descripción para no mezclar con el Plan C.
    "SEGUROS_COMERCIAL": {
        "plan":             "B",
        "etiqueta":         "Comercial de Seguros",
        "min_score":        4,
        "rol_en_titulo":    True,
        "contexto_exigido": ["seguro"],
        "roles": [
            "productor asesor", "productor de seguros", "productora de seguros",
            "asesor de seguros", "asesora de seguros", "asesor comercial de seguros",
            "asesor en seguros", "asesora en seguros", "ejecutivo de seguros",
            "ejecutiva de seguros", "ejecutivo comercial de seguros",
            "ejecutiva comercial de seguros", "comercial de seguros",
            "comercial en seguros", "vendedor de seguros", "vendedora de seguros",
            "venta de seguros", "ventas de seguros", "ejecutivo de ventas de seguros",
            "agente de seguros", "corredor de seguros", "consultor de seguros",
            "broker de seguros", "asesor de seguros de vida", "asesora de seguros de vida",
            "asesor de seguros generales", "asesora de seguros generales",
            "ventas de polizas", "renovacion de polizas", "asesor de rentas",
            "asesor de siniestros y ventas", "asesor comercial de vida",
            "asesor comercial", "asesora comercial", "ejecutivo comercial",
            "ejecutiva comercial", "representante comercial", "asesor de ventas",
            "asesora de ventas", "ejecutivo de ventas", "ejecutiva de ventas",
            "consultor comercial",
        ],
        "contexto": [
            "seguros", "seguro", "poliza", "polizas", "prima", "primas",
            "cartera de clientes", "aseguradora", "compania de seguros",
            "broker", "renovacion de polizas", "emision de polizas",
            "matricula", "pms", "suscripcion", "reclamos", "cobertura",
            "endoso", "banca seguros", "bancaseguros", "seguros generales",
            "seguros de vida", "seguros patrimoniales", "seguros de salud",
            "cobranza de primas", "ramo", "ramos",
        ],
    },

    # ── PLAN C ── Ejecutivo de Cuentas / Key Account B2B (fuera de seguros)
    # Transferible de Unibell: cartera, pricing, e-commerce, CRM Oracle,
    # ciclo comercial completo. Roles genéricos (asesor/ejecutivo comercial)
    # exigen contexto B2B/cuentas para no atrapar ventas minoristas.
    "KAM_B2B": {
        "plan":             "C",
        "etiqueta":         "KAM / Cuentas B2B",
        "min_score":        4,
        "rol_en_titulo":    False,
        "contexto_exigido": [
            "b2b", "mayorista", "mayoristas", "distribuidor",
            "distribuidores", "corporativo", "corporativa", "industrial",
            "institucional", "wholesale", "canales", "prospeccion",
            "gestion comercial", "ventas corporativas", "cartera",
            "key account", "cuentas clave", "cuentas corporativas",
            "clientes corporativos",
        ],
        "roles": [
            "ejecutivo de cuentas", "ejecutiva de cuentas", "ejecutivo de cuenta",
            "key account", "kam", "cuentas clave", "gerente de cuentas",
            "account manager", "gestor de cuentas", "cuentas corporativas",
            "analista de cuentas", "representante de cuentas", "ejecutivo comercial",
            "ejecutiva comercial", "analista comercial", "gestor comercial",
            "representante comercial", "asesor comercial", "asesora comercial",
            "comercial b2b", "ventas b2b", "ventas corporativas",
            "ventas institucionales", "ventas mayoristas", "ventas por mayor",
            "comercial mayorista", "ejecutivo de ventas b2b", "key account manager",
            "desarrollo de negocios", "business development", "desarrollo comercial",
            "coordinador comercial", "comercial corporativo", "comercial internacional",
        ],
        "contexto": [
            "b2b", "cartera", "prospeccion", "crm", "negociacion",
            "distribuidores", "e-commerce", "ecommerce", "mercado libre",
            "canales", "pricing", "facturacion", "pedidos", "clientes",
            "ventas", "cuentas", "kpis", "reporting", "excel", "margenes",
            "cotizaciones", "marca", "portafolio",
        ],
    },

    # ── PLAN D ── Analista Administrativo / Back Office Comercial
    # Puerta de entrada rápida: pedidos, facturación, cobranzas, stock y
    # postventa (todo lo ya hecho en Unibell y Guaticobre). Útil para entrar
    # a una empresa grande mientras se evalúa A o B.
    "BACKOFFICE_ADMIN": {
        "plan":             "D",
        "etiqueta":         "Admin / Back Office",
        "min_score":        4,
        "rol_en_titulo":    False,
        "contexto_exigido": [],
        "roles": [
            "administrativo comercial", "administrativa comercial", "back office",
            "backoffice", "analista administrativo", "analista administrativa",
            "asistente administrativo", "asistente administrativa",
            "auxiliar administrativo", "auxiliar administrativa",
            "administrativo de ventas", "administrativa de ventas",
            "administrativo contable", "administrativo de operaciones",
            "operador administrativo", "administrativo de facturacion",
            "analista de facturacion", "asistente de facturacion",
            "administrativo de cobranzas", "analista de cobranzas",
            "asistente de cobranzas", "administrativo de stock",
            "control de stock", "analista de stock", "gestor de pedidos",
            "administrativo de pedidos", "asistente de pedidos",
            "analista de pedidos", "coordinador administrativo",
            "administrativo general", "asistente de operaciones",
            "asistente comercial", "analista de operaciones",
            "gestion de pedidos", "gestion de facturacion",
            "administrativo de compras", "asistente de compras",
            "atencion postventa", "postventa", "post-venta",
            "atencion post venta", "asesor de postventa",
            "asistente de ventas", "auxiliar de ventas",
        ],
        "contexto": [
            "pedidos", "facturacion", "facturas", "cobranzas", "stock",
            "inventario", "control de stock", "postventa", "clientes",
            "excel", "reportes", "sap", "erp", "ordenes", "orden de compra",
            "entrega", "proveedores", "compras", "back office", "agenda",
            "seguimiento", "administracion", "envio", "mtm", "catalogo",
        ],
    },
}

# ─────────────────────────────────────────────
#  EXCLUSIONES — FUERA DE PERFIL (Kevin Rondón)
#  Este perfil NO busca roles de hostelería/gastronomía, venta minorista
#  presencial, telemarketing/call center, trabajo manual/de depósito,
#  IT/consultoría ni contabilidad/auditoría.
# ─────────────────────────────────────────────

# 1) Términos de DESCRIPCIÓN: rechazan la vacante si aparecen en el TÍTULO
#    O en la DESCRIPCIÓN. Solo dominios claramente ajenos al perfil:
#    hostelería/gastronomía, telemarketing y venta al público presencial.
#    (Un término como "retail" o "mostrador" suele aparecer de forma
#    secundaria en descripciones válidas, por eso NO va acá.)
EXCLUSIONES_FUERTES = [
    # ── Hostelería / gastronomía / servicio ──
    "camarero", "camareo", "mozo", "moso", "garzon", "mesero", "mesonero",
    "salonero", "camarista", "mucama", "housekeeping", "bartender", "barman",
    "barista", "waiter", "restaurante", "cafeteria", "cantina", "gastronomia",
    "hosteleria", "hotel", "cocina", "cocinero", "ayudante de cocina", "chef",
    "lounge", "recepcionista",
    # ── Telemarketing / call center / encuestas ──
    "telemarketing", "call center", "callcenter", "encuestador",
    "teleoperador", "teleoperadora", "televendedor", "ventas telefonicas",
    "venta telefonica", "operador telefonico",
    # ── Venta al público presencial (frases inequívocas) ──
    "venta al publico", "puerta a puerta", "vendedor ambulante",
    "boca de expendio", "atencion al publico",
    "atencion al cliente presencial",
]

# 2) Términos de TÍTULO: rechazan la vacante solo si aparecen en el TÍTULO.
#    Roles claramente ajenos al perfil (venta presencial, retail, puestos
#    manuales, IT, contabilidad). No se aplican a la descripción para evitar
#    descartar vacantes válidas que los mencionan de forma secundaria.
#    IMPORTANTE: en el Plan B (comercial de seguros) se flexibilizan cuando el
#    título menciona "seguro/s" (p.ej. "Vendedor de Seguros" es válido).
EXCLUSIONES_TITULO = [
    # ── Venta presencial / retail / promoción ──
    "vendedor", "vendedora", "vendedoras", "vendedores", "tienda", "showroom",
    "kiosco", "punto de venta", "comision por venta", "captacion de clientes",
    "fuerza de ventas", "venta directa", "promotor de ventas",
    "promotora de ventas", "preventista", "local de ventas",
    "vendedor de local", "venta minorista", "retail", "venta ambulante",
    "cajero", "reponedor", "repositor", "mostrador",
    # ── Telemarketing / televentas / call center (título) ──
    "televentas", "tele ventas", "vendedor por telefono", "call center",
    "campaign", "centro de atencion", "atencion al cliente",
    "servicio al cliente", "customer service", "customer support",
    "soporte al cliente", "mesa de ayuda",
    # ── Manual / depósito / transporte ──
    "operario", "operaria", "peon", "cadete", "repartidor", "chofer",
    "motorizado", "ayudante de deposito", "auxiliar de deposito",
    "ayudante de almacen", "auxiliar de almacen", "mozo de deposito",
    "operario de deposito", "montacarguista", "operador de deposito", "barra",
    "jefe de planta", "gerente de planta", "encargado de deposito",
    "encargado de local", "encargada de local", "encargado de almacen",
    "picking", "packer", "empacador",
    # ── IT / desarrollo / soporte técnico (fuera del perfil comercial) ──
    "desarrollador", "desarrolladora", "programador", "programadora",
    "ingeniero", "ingeniera", "analista de datos", "analista funcional",
    "data analyst", "data engineer", "ciencia de datos", "data scientist",
    "consultor", "consultora", "consultor sap",
    "consultor funcional", "soporte tecnico", "soporte it", "help desk",
    "sysadmin", "qa", "tester", "dba", "developer", "fullstack", "frontend",
    "backend", "devops", "ciberseguridad",
    # ── Contabilidad / auditoría ──
    "contador", "contadora", "auditor", "auditora", "impuestos", "tributario",
    "liquidacion de impuestos", "monotributo",
    # ── Otros ajenos ──
    "marketing", "community manager", "ventas por catalogo", "outlet",
    "vendor business", "recepcion",
]

# 3) Exclusiones por PLAN. Se bloquean roles que se cuelan solo por palabras
#    de la descripción. Para Kevin casi toda la precisión sale del matching de
#    rol (Plan A/B exigen rol en el título; Plan C exige contexto B2B), así
#    que aquí solo queda un pequeño corte de venta pura/roles ajenos.
EXCLUSIONES_TITULO_POR_PLAN = {
    "A": [
        "ejecutivo de ventas", "ejecutiva de ventas", "jefe de ventas",
        "gerente de ventas", "sales", "marketing", "community manager",
        "productor asesor de seguros", "productor de seguros",
    ],
    "B": [
        "sales representative", "sales specialist", "sales hunter",
        "inside sales", "field sales", "account executive",
        "analista de datos", "analista funcional", "desarrollador", "qa",
    ],
    "C": [
        "sales representative", "inside sales", "televentas", "ventas por telefono",
    ],
    "D": [
        "ejecutivo de ventas", "ejecutiva de ventas", "sales",
        "repartidor", "cadete", "asistente de deposito",
    ],
}


# ─────────────────────────────────────────────
#  PAÍSES
# ─────────────────────────────────────────────
COUNTRY_INDEED_MAP = {
    "Argentina":   "argentina",
    "Chile":       "chile",
    "Peru":        "peru",
    "Mexico":      "mexico",
    "Colombia":    "colombia",
    "Uruguay":     "uruguay",
    "Costa Rica":  "costa rica",
    "Spain":       "spain",
    "Venezuela":   "venezuela",
    "Ecuador":     "ecuador",
    "Panama":      "panama",
    "Paraguay":    "paraguay",
    "Bolivia":     "bolivia",
}

PAISES_HISPANOS_REMOTO = [
    "Chile", "Peru", "Mexico", "Colombia", "Uruguay",
    "Costa Rica", "Spain", "Venezuela", "Ecuador", "Panama",
    "Paraguay", "Bolivia",
]

PLANES_PAISES = {
    # Siniestros, seguros y back office son mercados locales → solo Argentina.
    "SINIESTROS":       [("Argentina", False)],
    "SEGUROS_COMERCIAL":[("Argentina", False)],
    "KAM_B2B":          [("Argentina", False)] + [(p, True) for p in PAISES_HISPANOS_REMOTO],
    "BACKOFFICE_ADMIN": [("Argentina", False)],
}

REMOTE_KEYWORDS = [
    "remote", "remoto", "100% remoto", "full remote", "trabajo remoto",
    "home office", "teletrabajo", "work from home", "wfh", "open to latam",
    "ubicacion: remoto", "remota",
]
HYBRID_KEYWORDS = ["hibrido", "hybrid", "presencial", "on-site", "onsite"]


# ─────────────────────────────────────────────
#  PERSISTENCIA — "YA VISTOS"
# ─────────────────────────────────────────────

def cargar_vistos():
    if os.path.exists(ARCHIVO_VISTOS):
        try:
            with open(ARCHIVO_VISTOS, "r", encoding="utf-8") as f:
                data = json.load(f)
                return set(data.get("urls", []))
        except (json.JSONDecodeError, IOError):
            return set()
    return set()


def guardar_vistos(vistos):
    data = {
        "ultima_actualizacion": datetime.now().isoformat(),
        "total_urls": len(vistos),
        "urls": list(vistos),
    }
    with open(ARCHIVO_VISTOS, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ─────────────────────────────────────────────
#  HELPERS DE FILTRADO
# ─────────────────────────────────────────────

def build_or_query(terminos):
    return " OR ".join(f'"{t}"' for t in terminos)


def puntuar(titulo_n, desc_n, roles, contexto):
    """Score ponderado: rol en título 4x, rol en descripción 2x,
    contexto en título 2x, contexto en descripción 1x."""
    score = 0
    hits_rol = []
    hits_ctx = []
    for r in roles:
        if r in titulo_n:
            score += 4
            hits_rol.append(r)
        if r in desc_n:
            score += 2
            hits_rol.append(r)
    for c in contexto:
        if c in titulo_n:
            score += 2
            hits_ctx.append(c)
        if c in desc_n:
            score += 1
            hits_ctx.append(c)
    return score, list(dict.fromkeys(hits_rol)), list(dict.fromkeys(hits_ctx))


def evaluar_vacante(titulo, descripcion, perfil):
    """Devuelve (datos, motivo_rechazo) o (None, motivo) si no califica."""
    t = normalizar(titulo)
    d = normalizar(descripcion)[:5000]

    # Los planes A/B trabajan dominios muy específicos en el título
    # ("siniestros" / "seguro"). Si el título ya los menciona, se flexibilizan
    # las exclusiones genéricas de venta para no tirar roles válidos como
    # "Consultor/a de Siniestros ART" o "Vendedor/a de Seguros".
    dominio_fuerte = (
        (perfil["plan"] == "A" and (
            "siniestros" in t or ("reclamo" in t and "seguro" in t)))
        or (perfil["plan"] == "B" and "seguro" in t)
    )

    if any(x in t for x in EXCLUSIONES_TITULO) and not dominio_fuerte:
        return None, "EXCLUIDA (título fuera de perfil)"
    if any(x in t for x in EXCLUSIONES_FUERTES) or any(x in d for x in EXCLUSIONES_FUERTES):
        return None, "EXCLUIDA (hostelería/retail/telemarketing/manual)"

    # Exclusiones específicas del plan (venta pura / rol ajeno)
    if any(x in t for x in EXCLUSIONES_TITULO_POR_PLAN.get(perfil["plan"], [])):
        return None, "EXCLUIDA (venta pura / rol ajeno al plan)"

    score, hits_rol, hits_ctx = puntuar(t, d, perfil["roles"], perfil["contexto"])

    if not hits_rol:
        return None, "sin término de rol (solo skill coincidente)"

    # "Contexto exigido": roles genéricos necesitan refuerzo de dominio.
    # Plan B exige "seguro/s"; Plan C (KAM) exige contexto B2B/cuentas.
    exigido = perfil.get("contexto_exigido", [])
    if exigido and not any(c in t or c in d for c in exigido):
        return None, "sin contexto requerido del plan (B2B/seguros)"

    # Para planes con rol_en_titulo=True (A y B) se exige el rol en el TÍTULO,
    # o evidencia muy fuerte en la descripción (>= 2 términos de rol distintos
    # y score alto). Así se descartan contadores, sales reps, técnicos, etc.
    # que solo mencionan palabras del perfil en la descripción.
    if perfil.get("rol_en_titulo", True):
        hits_rol_titulo = [r for r in perfil["roles"] if r in t]
        if not hits_rol_titulo:
            if len(hits_rol) < 2 or score < 14:
                return None, f"rol solo en descripción (score {score})"

    if score < perfil["min_score"]:
        return None, f"score bajo ({score} < {perfil['min_score']})"

    return {
        "score":        score,
        "hits_rol":     hits_rol,
        "hits_ctx":     hits_ctx,
        "terminos":     ", ".join(hits_rol + hits_ctx),
        "titulo_n":     t,
    }, None


def es_espanol(texto):
    if not texto or len(str(texto).strip()) < 50:
        return False
    palabras_es = ["beneficios", "prestaciones", "vacante", "equipo",
                   "experiencia", "postularse", "desarrollo"]
    contiene_es = any(p in texto.lower() for p in palabras_es)
    try:
        es_es = detect(texto) == 'es'
    except LangDetectException:
        es_es = False
    return es_es or contiene_es


def es_remoto_real(ubicacion, descripcion, is_remote_flag):
    texto = f"{ubicacion} {str(descripcion)[:1000]}".lower()
    if any(k in texto for k in HYBRID_KEYWORDS):
        return False
    if is_remote_flag is True:
        return True
    if any(k in texto for k in REMOTE_KEYWORDS):
        return True
    return False


# ─────────────────────────────────────────────
#  FILTRO GEOGRÁFICO — Modo Argentina
#  Solo CABA / GBA / Provincia de Buenos Aires
# ─────────────────────────────────────────────

# Claves que indican CABA, Buenos Aires, GBA o Provincia de Buenos Aires
CABA_GBA_KEYS = (
    "caba", "capital federal", "ciudad autonoma de buenos aires",
    "ciudad de buenos aires", "buenos aires", "bs as", "bsas",
    "gba", "gran buenos aires", "conurbano", "amba",
    "zona norte", "zona sur", "zona oeste", "zona noroeste", "zona sudoeste",
    "provincia de buenos aires",
)

# Barrios de CABA y municipios del AMBA/GBA + ciudades de la Prov. de Bs. As.
BA_CITIES = (
    # CABA / barrios porteños
    "palermo", "recoleta", "belgrano", "nunez", "saavedra", "devoto",
    "villa urquiza", "caballito", "almagro", "boedo", "san telmo",
    "la boca", "barracas", "monserrat", "constitucion", "retiro",
    "puerto madero", "floresta", "villa crespo", "parque patricios",
    "villa luro", "liniers", "mataderos", "monte castro", "villa del parque",
    "coghlan", "villa ortuzar", "chacarita", "villa santa rita", "versalles",
    # Partidos del conurbano / AMBA
    "avellaneda", "quilmes", "lanus", "lomas de zamora", "almirante brown",
    "berazategui", "florencio varela", "ezeiza", "esteban echeverria",
    "presidente peron", "san vicente", "la matanza", "moron", "hurlingham",
    "ituzaingo", "merlo", "moreno", "general rodriguez", "marcos paz",
    "jose c paz", "malvinas argentinas", "pilar", "escobar", "tigre",
    "san fernando", "san isidro", "vicente lopez", "san martin",
    "tres de febrero", "san miguel", "general pacheco", "el palomar",
    "caseros", "ramos mejia", "castelar", "haedo", "ciudadela",
    "villa ballester", "san andres", "olivos", "martinez", "acassuso",
    "florida", "munro", "carapachay", "boulogne", "don torcuato",
    "victoria", "rincon de milberg", "la lucila", "saenz pena",
    # Resto de la Provincia de Buenos Aires
    "la plata", "berisso", "ensenada", "mar del plata", "bahia blanca",
    "tandil", "olavarria", "junin", "pergamino", "chivilcoy", "azul",
    "lujan", "mercedes", "san nicolas", "campana", "zarate",
    "pinamar", "villa gesell", "necochea", "dolores", "ramallo",
    "san pedro", "chacabuco", "nueve de julio", "rojas", "lincoln",
)

# Provincias y ciudades FUERA de Buenos Aires (se descartan en modo Argentina)
NO_BA_CITIES = (
    "rosario", "santa fe", "cordoba", "mendoza", "bariloche", "rio negro",
    "neuquen", "salta", "jujuy", "tucuman", "santiago del estero",
    "catamarca", "la rioja", "san juan", "san luis", "misiones",
    "corrientes", "entre rios", "formosa", "chaco", "la pampa",
    "tierra del fuego", "ushuaia", "santa cruz", "chubut",
    "comodoro rivadavia", "trelew", "puerto madryn", "rawson", "esquel",
    "viedma", "general roca", "cipolletti", "posadas", "resistencia",
    "parana", "concordia", "gualeguaychu", "concepcion del uruguay",
    "santa rosa", "rio cuarto", "villa maria", "san rafael", "zapala",
    "cutral co", "caleta olivia", "rio gallegos", "el calafate",
    "perito moreno", "godoy cruz", "guaymallen", "las heras",
    "lujan de cuyo", "san martin de los andes", "villa la angostura",
    "el bolson", "san salvador de jujuy", "palpala", "cafayate", "tilcara",
    "puerto iguazu", "termas de rio hondo",
)


def es_ubicacion_buenos_aires(ubicacion):
    """True si la ubicación es CABA, GBA o Provincia de Buenos Aires.
    Se aplica SOLO en el flujo Argentina (MODO_ARGENTINA).
    Vacantes remotas o sin ubicación especificada NO se filtran."""
    t = normalizar(str(ubicacion))
    if not t:
        return True

    # Remoto puro (sin ciudad fuera de la provincia de Buenos Aires)
    if any(k in t for k in REMOTE_KEYWORDS) and not any(k in t for k in NO_BA_CITIES):
        return True

    # CABA / Buenos Aires / GBA por nombre directo
    if any(k in t for k in CABA_GBA_KEYS):
        return True

    # Provincias / ciudades fuera de Buenos Aires → descartar
    if any(k in t for k in NO_BA_CITIES):
        return False

    # Municipios del AMBA y ciudades de la Prov. de Bs. As.
    if any(k in t for k in BA_CITIES):
        return True

    # Ubicación no reconocible → no filtrar (remoto sin especificar, etc.)
    return True


# ─────────────────────────────────────────────
#  BÚSQUEDA POR PERFIL
# ─────────────────────────────────────────────

def categorias_para_modo(modo):
    """Categorías que se buscan según el modo de ejecución.

    Modo PAÍSES (remoto): solo categorías con vacantes fuera de Argentina
    en PLANES_PAISES. Para Kevin eso es únicamente Plan C (KAM B2B remoto);
    siniestros y seguros son mercados locales (Argentina)."""
    if modo == MODO_PAISES:
        return [
            c for c in PERFILES
            if any(p != "Argentina" for p, _ in PLANES_PAISES[c])
        ]
    return list(PERFILES.keys())


def paises_para_categoria(categoria, modo):
    """Países a buscar para una categoría según el modo de ejecución."""
    if modo == MODO_ARGENTINA:
        return [("Argentina", False)]
    if modo == MODO_PAISES:
        return [(p, True) for p in PAISES_HISPANOS_REMOTO]
    return PLANES_PAISES[categoria]


def evaluar_crudo(cr, categoria, perfil):
    """Evalúa un dict crudo estandarizado (JobSpy, Computrabajo, SPA...).

    Devuelve (resultado, None) si califica, o (None, motivo) si no.
    El resultado ya trae todos los campos listos para la tabla final.
    """
    titulo         = cr["titulo"]
    empresa        = cr["empresa"]
    ubicacion      = cr["ubicacion"]
    descripcion    = cr["descripcion"]
    url            = cr["url"]
    is_remote_flag = cr.get("is_remote_flag")
    pais           = cr.get("pais_busqueda", "Argentina")

    datos, motivo = evaluar_vacante(titulo, descripcion, perfil)
    if datos is None:
        return None, motivo

    if MODO_ACTUAL == MODO_ARGENTINA and not es_ubicacion_buenos_aires(ubicacion):
        return None, "ubicación fuera de CABA/GBA/Prov. Buenos Aires"

    remoto_real = es_remoto_real(ubicacion, descripcion, is_remote_flag)
    if pais != "Argentina" and not remoto_real:
        return None, "no es remoto (se exige remoto en este país)"

    return {
        "plan":          perfil["plan"],
        "categoria":     categoria,
        "etiqueta":      perfil["etiqueta"],
        "pais_busqueda": pais,
        "titulo":        titulo,
        "empresa":       empresa,
        "ubicacion":     ubicacion,
        "remoto":        remoto_real,
        "idioma_es":     es_espanol(descripcion),
        "score":         datos["score"],
        "terminos_match": datos["terminos"],
        "url":            url,
        "fuente":         cr.get("sitio_origen", ""),
    }, None


def _crudos_secundarios(categoria):
    """Recolecta crudos de Computrabajo y Bumeran/ZonaJobs según el modo."""
    from fuente_computrabajo import extraer_computrabajo

    crudos = []

    if MODO_ACTUAL == MODO_ARGENTINA:
        print("   [>] Fuente secundaria: computrabajo (solo Argentina)")
        crudos.extend(extraer_computrabajo(categoria, paises=["ar"]))
    elif MODO_ACTUAL == MODO_PAISES:
        from fuente_computrabajo import PAISES_COMPUTRABAJO

        remotos = [c for c in PAISES_COMPUTRABAJO if c != "ar"]
        print(f"   [>] Fuente secundaria: computrabajo ({len(remotos)} países, solo remoto)")
        crudos.extend(extraer_computrabajo(categoria, paises=remotos))
    else:
        print("   [>] Fuente secundaria: computrabajo (todos los países)")
        crudos.extend(extraer_computrabajo(categoria))

    if MODO_ACTUAL != MODO_PAISES:
        from fuente_spa import extraer_spa

        print("   [>] Fuente secundaria: bumeran/zonajobs (Argentina)")
        crudos.extend(extraer_spa(categoria))

    return crudos


def buscar_perfil(categoria):
    perfil = PERFILES[categoria]
    plan   = perfil["plan"]
    print(f"\n{'='*70}")
    print(f"  PERFIL: {categoria}  ({perfil['etiqueta']})  → Plan {plan}")
    print(f"  MODO:   {MODO_ACTUAL}")
    print(f"{'='*70}")

    # La query de búsqueda usa SOLO términos de rol (más preciso en LinkedIn).
    terminos = perfil["roles"]
    query    = build_or_query(terminos)
    print(f"  Query OR (roles): {query[:130]}...")
    print(f"  Filtro relevancia: score >= {perfil['min_score']} + al menos 1 rol")

    resultados       = []
    rechazadas_ar    = []

    def procesar_crudo(cr, descartados):
        """Evalúa un crudo; actualiza resultados/rechazadas y el contador."""
        res, motivo = evaluar_crudo(cr, categoria, perfil)

        if res is None:
            descartados += 1
            if DEBUG:
                print(f"      ✗ {motivo} -> '{cr['titulo']}'")
            if (
                motivo and "EXCLUIDA" not in motivo
                and cr.get("pais_busqueda", "Argentina") == "Argentina"
                and es_espanol(cr["descripcion"])
            ):
                rechazadas_ar.append({
                    "plan":          plan,
                    "categoria":     categoria,
                    "pais_busqueda": cr.get("pais_busqueda", "Argentina"),
                    "titulo":        cr["titulo"],
                    "empresa":       cr["empresa"],
                    "ubicacion":     cr["ubicacion"],
                    "score":         0,
                    "coincidencias": 1,
                    "terminos_match": motivo,
                    "url":            cr["url"],
                    "fuente":         cr.get("sitio_origen", ""),
                })
            return descartados

        if DEBUG:
            print(f"      ✅ APTO (score {res['score']}) -> '{cr['titulo']}'")

        resultados.append(res)
        return descartados

    for pais, requiere_remoto in paises_para_categoria(categoria, MODO_ACTUAL):
        print(f"\n  Buscando en {pais}{' (solo remoto)' if requiere_remoto else ''}...")

        try:
            jobs = scrape_jobs(
                site_name=["linkedin", "indeed"],
                search_term=query,
                location=pais,
                is_remote=requiere_remoto,
                results_wanted=CANTIDAD_POR_BUSQUEDA,
                hours_old=HORAS_ANTIGUEDAD,
                country_indeed=COUNTRY_INDEED_MAP.get(pais, pais.lower()),
                linkedin_fetch_description=True,
            )
        except Exception as e:
            print(f"   ⚠️ Error: {e}")
            continue

        if jobs is None or len(jobs) == 0:
            print("   (sin resultados)")
            continue

        print(f"   ✅ {len(jobs)} encontrados (antes de filtros)")

        descartados = 0
        for _, job in jobs.iterrows():
            cr = {
                "titulo":         str(job.get('title', '') or ''),
                "empresa":        str(job.get('company', '') or ''),
                "ubicacion":      str(job.get('location', '') or ''),
                "descripcion":    str(job.get('description', '') or ''),
                "url":            str(job.get('job_url', '') or ''),
                "is_remote_flag": job.get('is_remote', None),
                "pais_busqueda":  pais,
                "sitio_origen":   str(job.get('site', '') or ''),
            }
            descartados = procesar_crudo(cr, descartados)

        if descartados:
            print(f"   ↳ {descartados} descartados/excluidos")

        time.sleep(2)

    # ── Fuentes secundarias (Computrabajo + Bumeran/ZonaJobs) ──
    descartados = 0
    for cr in _crudos_secundarios(categoria):
        descartados = procesar_crudo(cr, descartados)
    if descartados:
        print(f"   ↳ {descartados} descartados/excluidos en fuentes secundarias")

    return resultados, rechazadas_ar


# ─────────────────────────────────────────────
#  HTML — Vacantes que califican
# ─────────────────────────────────────────────

COLORES_PLAN = {"A": "#1F4E79", "B": "#2E7D32", "C": "#A04040", "D": "#6A5ACD"}

def construir_html(df_total):
    if df_total.empty:
        return "<p>No se encontraron vacantes NUEVAS en esta corrida.</p>"

    filas_html = ""
    for _, row in df_total.iterrows():
        remoto_tag  = "🌍 Remoto" if row["remoto"] else "—"
        color_plan  = COLORES_PLAN.get(row["plan"], "#333")
        badge       = f'<span style="background:{color_plan};color:white;padding:2px 7px;border-radius:4px;font-weight:bold;">Plan {row["plan"]}</span>'
        filas_html += f"""
        <tr>
            <td style="padding:6px; border:1px solid #ddd; text-align:center;">{badge}</td>
            <td style="padding:6px; border:1px solid #ddd;">
                <a href="{row['url']}" style="color:#1a0dab;">{row['titulo']}</a>
            </td>
            <td style="padding:6px; border:1px solid #ddd;">{row['empresa']}</td>
            <td style="padding:6px; border:1px solid #ddd;">{row['ubicacion']}</td>
            <td style="padding:6px; border:1px solid #ddd; text-align:center;">{remoto_tag}</td>
            <td style="padding:6px; border:1px solid #ddd; font-size:11px; color:#555;">{row['etiqueta']}</td>
            <td style="padding:6px; border:1px solid #ddd; text-align:center; font-weight:bold;">{row['score']}</td>
            <td style="padding:6px; border:1px solid #ddd; font-size:11px;">{row['terminos_match']}</td>
            <td style="padding:6px; border:1px solid #ddd; font-size:11px; color:#555;">{row['fuente']}</td>
        </tr>"""

    cnt_a = len(df_total[df_total['plan'] == 'A'])
    cnt_b = len(df_total[df_total['plan'] == 'B'])
    cnt_c = len(df_total[df_total['plan'] == 'C'])
    cnt_d = len(df_total[df_total['plan'] == 'D'])

    html = f"""
    <html>
    <body style="font-family: Arial, sans-serif; max-width: 1100px; margin: auto;">
        <h2 style="color:#1F4E79;">Vacantes NUEVAS encontradas — {datetime.now().strftime('%d/%m/%Y %H:%M')}</h2>
        <p>
            Total: <strong>{len(df_total)}</strong> vacantes nuevas &nbsp;|&nbsp;
            <span style="color:{COLORES_PLAN['A']}; font-weight:bold;">Plan A: {cnt_a}</span> &nbsp;|&nbsp;
            <span style="color:{COLORES_PLAN['B']}; font-weight:bold;">Plan B: {cnt_b}</span> &nbsp;|&nbsp;
            <span style="color:{COLORES_PLAN['C']}; font-weight:bold;">Plan C: {cnt_c}</span> &nbsp;|&nbsp;
            <span style="color:{COLORES_PLAN['D']}; font-weight:bold;">Plan D: {cnt_d}</span>
        </p>
        <table style="border-collapse: collapse; width: 100%; font-size: 13px;">
            <thead>
                <tr style="background-color:#1F4E79; color:white;">
                    <th style="padding:6px; border:1px solid #ddd;">Plan</th>
                    <th style="padding:6px; border:1px solid #ddd;">Título</th>
                    <th style="padding:6px; border:1px solid #ddd;">Empresa</th>
                    <th style="padding:6px; border:1px solid #ddd;">Ubicación</th>
                    <th style="padding:6px; border:1px solid #ddd;">Modalidad</th>
                    <th style="padding:6px; border:1px solid #ddd;">Categoría</th>
                    <th style="padding:6px; border:1px solid #ddd;">Score</th>
                    <th style="padding:6px; border:1px solid #ddd;">Coincidencias</th>
                    <th style="padding:6px; border:1px solid #ddd;">Origen</th>
                </tr>
            </thead>
            <tbody>
                {filas_html}
            </tbody>
        </table>
        <p style="font-size:11px; color:#888; margin-top:20px;">
            Generado automáticamente por alerta_empleos_linkedin.py (v12 · Kevin · multi-fuente)
        </p>
    </body>
    </html>
    """
    return html


# ─────────────────────────────────────────────
#  HTML — Rechazadas (casi califican)
# ─────────────────────────────────────────────

def construir_html_rechazadas(df_rechazadas):
    if df_rechazadas.empty:
        return "<p>No hubo vacantes 'casi calificadas' NUEVAS en Argentina en esta corrida.</p>"

    filas_html = ""
    for _, row in df_rechazadas.iterrows():
        filas_html += f"""
        <tr>
            <td style="padding:6px; border:1px solid #ddd; font-weight:bold;">{row['plan']}</td>
            <td style="padding:6px; border:1px solid #ddd;">
                <a href="{row['url']}">{row['titulo']}</a>
            </td>
            <td style="padding:6px; border:1px solid #ddd;">{row['empresa']}</td>
            <td style="padding:6px; border:1px solid #ddd;">{row['ubicacion']}</td>
            <td style="padding:6px; border:1px solid #ddd; font-size:11px;">{row['categoria']}</td>
            <td style="padding:6px; border:1px; text-align:center;">{row['coincidencias']}</td>
            <td style="padding:6px; border:1px solid #ddd; font-size:11px;">{row['terminos_match']}</td>
            <td style="padding:6px; border:1px solid #ddd; font-size:11px; color:#555;">{row['fuente']}</td>
        </tr>"""

    html = f"""
    <html>
    <body style="font-family: Arial, sans-serif; max-width: 1100px; margin: auto;">
        <h2 style="color:#A04040;">Vacantes rechazadas NUEVAS — Argentina (casi califican)</h2>
        <p>Total: <strong>{len(df_rechazadas)}</strong> vacantes con al menos 1 coincidencia pero por debajo del mínimo.</p>
        <table style="border-collapse: collapse; width: 100%; font-size: 13px;">
            <thead>
                <tr style="background-color:#A04040; color:white;">
                    <th style="padding:6px; border:1px solid #ddd;">Plan</th>
                    <th style="padding:6px; border:1px solid #ddd;">Título</th>
                    <th style="padding:6px; border:1px solid #ddd;">Empresa</th>
                    <th style="padding:6px; border:1px solid #ddd;">Ubicación</th>
                    <th style="padding:6px; border:1px solid #ddd;">Categoría</th>
                    <th style="padding:6px; border:1px solid #ddd;">Matches</th>
                    <th style="padding:6px; border:1px solid #ddd;">Motivo</th>
                    <th style="padding:6px; border:1px solid #ddd;">Origen</th>
                </tr>
            </thead>
            <tbody>
                {filas_html}
            </tbody>
        </table>
    </body>
    </html>
    """
    return html


def guardar_resultados(df_aptas, df_rechazadas):
    """Guarda un snapshot de la corrida (aptas + rechazadas) para auditar."""
    def _filas(df):
        if df.empty:
            return []
        return [r.to_dict() for _, r in df.iterrows()]

    data = {
        "ultima_actualizacion": datetime.now().isoformat(),
        "aptas": _filas(df_aptas),
        "rechazadas": _filas(df_rechazadas),
    }
    try:
        with open("resultados_ultima_corrida.json", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        print(f"  📄 'resultados_ultima_corrida.json' guardado "
              f"({len(data['aptas'])} aptas / {len(data['rechazadas'])} rechazadas)")
    except IOError as e:
        print(f"  ⚠️ No se pudo guardar resultados_ultima_corrida.json: {e}")


# ─────────────────────────────────────────────
#  EMAIL
# ─────────────────────────────────────────────

def enviar_email(html_content, total_vacantes, asunto_base=None):
    asunto_base = asunto_base or EMAIL_ASUNTO
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"{asunto_base} ({total_vacantes})"
    msg["From"]    = EMAIL_REMITENTE
    msg["To"]      = EMAIL_DESTINATARIO
    msg.attach(MIMEText(html_content, "html"))

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(EMAIL_REMITENTE, EMAIL_APP_PASSWORD)
            server.sendmail(EMAIL_REMITENTE, EMAIL_DESTINATARIO, msg.as_string())
        print(f"\n📧 Email '{asunto_base}' enviado a {EMAIL_DESTINATARIO}")
    except Exception as e:
        print(f"\n⚠️ Error enviando email '{asunto_base}': {e}")


# ─────────────────────────────────────────────
#  CORRIDA PRINCIPAL
# ─────────────────────────────────────────────

def ejecutar_corrida():
    print(f"\n\n{'#'*70}")
    print(f"#  INICIANDO CORRIDA — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"#  MODO: {MODO_ACTUAL}")
    print(f"{'#'*70}")

    vistos = cargar_vistos()
    print(f"  URLs ya vistas en corridas anteriores: {len(vistos)}")

    todas_filas         = []
    todas_rechazadas_ar = []

    for categoria in categorias_para_modo(MODO_ACTUAL):
        encontradas, rechazadas_ar = buscar_perfil(categoria)
        todas_filas.extend(encontradas)
        todas_rechazadas_ar.extend(rechazadas_ar)

    df_total      = pd.DataFrame(todas_filas)
    df_rechazadas = pd.DataFrame(todas_rechazadas_ar)

    nuevas_urls_para_guardar = set()

    # ── Procesar vacantes que SÍ califican ──
    if df_total.empty:
        print("\nNo se encontraron resultados (calificadas) en esta corrida.")
        df_nuevas = pd.DataFrame()
    else:
        df_total  = df_total[df_total["idioma_es"] != False]
        df_total  = df_total.drop_duplicates(subset=["url"])
        df_total  = df_total.drop_duplicates(subset=["titulo","empresa"], keep="first")
        df_total  = df_total.sort_values(by=["plan", "score"], ascending=[True, False])

        df_nuevas = df_total[~df_total["url"].isin(vistos)]

        print(f"\n  Total calificadas (antes de filtro 'vistos'): {len(df_total)}")
        print(f"  NUEVAS (no enviadas antes): {len(df_nuevas)}")
        for plan in ["A", "B", "C", "D"]:
            print(f"    Plan {plan}: {len(df_nuevas[df_nuevas['plan']==plan])}")

        nuevas_urls_para_guardar.update(df_total["url"].tolist())

    # ── Guardar resultados de la corrida (para auditoría) ──
    guardar_resultados(df_total, df_rechazadas)

    html = construir_html(df_nuevas if not df_total.empty else pd.DataFrame())
    enviar_email(html, len(df_nuevas) if not df_total.empty else 0)

    # ── Procesar rechazadas (casi califican) ──
    if not df_rechazadas.empty:
        df_rechazadas          = df_rechazadas.drop_duplicates(subset=["url"])
        df_rechazadas_nuevas   = df_rechazadas[~df_rechazadas["url"].isin(vistos)]
        df_rechazadas_nuevas   = df_rechazadas_nuevas.sort_values(by="coincidencias", ascending=False)
        print(f"\n  Rechazadas Argentina NUEVAS (casi califican): {len(df_rechazadas_nuevas)}")
        nuevas_urls_para_guardar.update(df_rechazadas["url"].tolist())
    else:
        df_rechazadas_nuevas = pd.DataFrame()
        print("\n  Rechazadas Argentina NUEVAS (casi califican): 0")

    html_rechazadas = construir_html_rechazadas(df_rechazadas_nuevas)
    if not df_rechazadas_nuevas.empty:
        enviar_email(html_rechazadas, len(df_rechazadas_nuevas), asunto_base=EMAIL_ASUNTO_RECHAZADAS)

    # ── Actualizar archivo de "vistos" ──
    vistos.update(nuevas_urls_para_guardar)
    guardar_vistos(vistos)
    print(f"\n  💾 '{ARCHIVO_VISTOS}' actualizado. Total URLs guardadas: {len(vistos)}")
    print(f"\n{'#'*70}")
    print(f"#  CORRIDA FINALIZADA — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'#'*70}\n")


# ─────────────────────────────────────────────
#  MODO PROGRAMADO
# ─────────────────────────────────────────────

def modo_programado():
    print("🤖 Bot de vacantes iniciado en modo PROGRAMADO")
    print(f"⏰ Horarios de ejecución: {', '.join(HORARIOS_EJECUCION)}")
    print(f"🌎 Modo de búsqueda: {MODO_ACTUAL}")
    print("🛑 Para detener: Ctrl + C\n")

    for hora in HORARIOS_EJECUCION:
        schedule.every().day.at(hora).do(ejecutar_corrida)

    print(f"  Hora actual: {datetime.now().strftime('%H:%M:%S')}")
    print("  Esperando próximo horario programado...\n")

    while True:
        schedule.run_pending()
        time.sleep(30)


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────

def parse_args(argv):
    """Interpreta los argumentos y define el modo de búsqueda global."""
    global MODO_ACTUAL
    if "--solo-argentina" in argv:
        MODO_ACTUAL = MODO_ARGENTINA
    elif "--solo-paises" in argv:
        MODO_ACTUAL = MODO_PAISES
    else:
        MODO_ACTUAL = MODO_COMPLETO
    return MODO_ACTUAL


if __name__ == "__main__":
    parse_args(sys.argv)
    if "--once" in sys.argv:
        ejecutar_corrida()
    else:
        modo_programado()
