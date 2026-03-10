import os
import time
import glob
import signal
import logging
import threading
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service as BrowserService
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager

# ══════════════════════════════════════════════════════════════════════════════
# CONFIG — ajusta estos valores antes de ejecutar
# ══════════════════════════════════════════════════════════════════════════════

RUC   = os.environ.get("SRI_RUC", "") # Tu RUC o cédula
CLAVE = os.environ.get("SRI_CLAVE", "") # Tu clave del portal SRI en línea

# Rango de meses a descargar
MES_INICIO  = 12
AÑO_INICIO  = 2025
MES_FIN     = 12
AÑO_FIN     = 2025

# Directorio base donde se guardarán los archivos
# Se crea la estructura: DIRECTORIO_DESCARGA/YYYY-MM/
DIRECTORIO_DESCARGA = str(Path(__file__).parent / "sri_comprobantes")

# Ruta al ejecutable del navegador
BROWSER_PATH = "/usr/bin/google-chrome"

# Espera máxima en segundos para que aparezcan los elementos
TIMEOUT = 20

# Pausa entre descargas (segundos) — evita sobrecargar el servidor
PAUSA_ENTRE_DESCARGAS = 1.5

# IDs de los botones de descarga en la tabla (patrón: prefijo + índice_fila + sufijo)
# El portal usa JSF con índice 0-based por fila
ID_PREFIX   = "frmPrincipal:tablaCompRecibidos"
ID_SUFIJO_XML = "lnkXml"
ID_SUFIJO_PDF = "lnkPdf"

# ══════════════════════════════════════════════════════════════════════════════
# URLS DEL PORTAL SRI
# ══════════════════════════════════════════════════════════════════════════════

URL_LOGIN        = "https://srienlinea.sri.gob.ec/auth/realms/Internet/protocol/openid-connect/auth"
URL_COMPROBANTES = "https://srienlinea.sri.gob.ec/facturacion-internet/pages/comprobantes/consultaComprobantes.jsf"

# ══════════════════════════════════════════════════════════════════════════════
# LOGGING
# ══════════════════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("sri_descarga.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# CONTROL DE EJECUCIÓN — Ctrl+C para terminar, Ctrl+Z para pausar/reanudar
# ══════════════════════════════════════════════════════════════════════════════

_pausado   = threading.Event()
_pausado.set()   # set = corriendo, clear = pausado
_terminado = threading.Event()


def _handler_sigtstp(signum, frame):
    """Ctrl+Z — alterna entre pausar y reanudar."""
    if _pausado.is_set():
        _pausado.clear()
        print("\n⏸  Ejecución PAUSADA. Presiona Ctrl+Z de nuevo para reanudar.")
    else:
        _pausado.set()
        print("\n▶  Ejecución REANUDADA.")


def _handler_sigint(signum, frame):
    """Ctrl+C — detiene la ejecución limpiamente."""
    _terminado.set()
    _pausado.set()   # desbloquear si estaba pausado
    print("\n🛑  Interrupción recibida. Finalizando la ejecución actual...")


def registrar_señales():
    signal.signal(signal.SIGINT,  _handler_sigint)
    signal.signal(signal.SIGTSTP, _handler_sigtstp)


def punto_de_control(mensaje=""):
    """
    Llamar en puntos clave del loop. Bloquea si está pausado,
    lanza SystemExit si se pidió terminar.
    """
    _pausado.wait()   # bloquea mientras esté pausado
    if _terminado.is_set():
        raise SystemExit("Ejecución terminada por el usuario.")


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def esperar(driver, by, selector, timeout=TIMEOUT):
    """Espera hasta que el elemento sea visible y lo devuelve."""
    return WebDriverWait(driver, timeout).until(
        EC.visibility_of_element_located((by, selector))
    )


def esperar_clickable(driver, by, selector, timeout=TIMEOUT):
    """Espera hasta que el elemento sea clickeable y lo devuelve."""
    return WebDriverWait(driver, timeout).until(
        EC.element_to_be_clickable((by, selector))
    )


def directorio_mes(año, mes):
    """Devuelve (y crea si no existe) el directorio para el mes/año dado."""
    ruta = Path(DIRECTORIO_DESCARGA) / f"{año}-{mes:02d}"
    ruta.mkdir(parents=True, exist_ok=True)
    return str(ruta)


def configurar_driver(dir_descarga):
    """Inicializa Chrome con opciones de descarga automática sin diálogo."""
    prefs = {
        "download.default_directory": dir_descarga,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True,
        "plugins.always_open_pdf_externally": True,   # descarga PDF en vez de abrirlo
        "download.open_pdf_in_system_reader": False,
        "download_restrictions": 0,
        "profile.default_content_setting_values.automatic_downloads": 1,
        "credentials_enable_service": False,
        "profile.password_manager_enabled": False,
    }

    opciones = Options()
    opciones.binary_location = BROWSER_PATH
    opciones.add_experimental_option("prefs", prefs)

    # Descomenta la siguiente línea para correr en modo invisible (headless)
    # opciones.add_argument("--headless=new")

    opciones.add_argument("--no-sandbox")
    opciones.add_argument("--disable-dev-shm-usage")
    opciones.add_argument("--disable-blink-features=AutomationControlled")
    opciones.add_experimental_option("excludeSwitches", ["enable-automation"])
    opciones.add_experimental_option("useAutomationExtension", False)

    servicio = BrowserService(
        ChromeDriverManager().install()
    )

    driver = webdriver.Chrome(service=servicio, options=opciones)
    driver.maximize_window()
    return driver


# ══════════════════════════════════════════════════════════════════════════════
# AUTENTICACIÓN
# ══════════════════════════════════════════════════════════════════════════════

def hacer_login(driver):
    """
    Navega al portal SRI y realiza el login.
    URL de inicio : https://srienlinea.sri.gob.ec/sri-en-linea/inicio/NAT
    IDs confirmados:
      Enlace login : href="/sri-en-linea/contribuyente/perfil"  (aria-label)
      Usuario      : id="usuario"
      Contraseña   : id="password"
      Botón        : id="kc-login"
    """
    log.info("Navegando a la página de inicio SRI...")
    driver.get("https://srienlinea.sri.gob.ec/sri-en-linea/inicio/NAT")

    # Clic en el enlace "Iniciar sesión" del topbar (Angular, puede tardar en renderizar)
    btn_iniciar = esperar_clickable(driver, By.CSS_SELECTOR,
        "a[aria-label='Ir a iniciar sesión']"
    )
    btn_iniciar.click()
    log.info("Clic en Iniciar sesión.")

    # Esperar a que cargue el formulario de login (Keycloak)
    campo_usuario = esperar(driver, By.ID, "usuario")
    campo_usuario.clear()
    campo_usuario.send_keys(RUC)
    log.info(f"Usuario ingresado")

    campo_clave = esperar(driver, By.ID, "password")
    campo_clave.clear()
    campo_clave.send_keys(CLAVE)
    log.info(f"Clave ingresada")

    btn_submit = esperar_clickable(driver, By.ID, "kc-login")
    btn_submit.click()
    log.info("Formulario de login enviado, esperando redirección...")

    # Esperar redirección al portal (desaparece el formulario de login)
    try:
        WebDriverWait(driver, TIMEOUT).until(
            EC.url_contains("sri-en-linea/contribuyente")
        )
        log.info("Login exitoso.")
    except TimeoutException:
        # Verificar si hay mensaje de error en la página
        try:
            error = driver.find_element(By.CSS_SELECTOR, ".alert-error, #input-error, .kc-feedback-text")
            log.error(f"Error de login: {error.text}")
        except NoSuchElementException:
            log.error("Login falló: no hubo redirección. Verifica RUC/clave o si hay CAPTCHA activo.")
        raise


# ══════════════════════════════════════════════════════════════════════════════
# NAVEGACIÓN AL MÓDULO DE COMPROBANTES RECIBIDOS
# ══════════════════════════════════════════════════════════════════════════════

def navegar_comprobantes_recibidos(driver):
    """
    Navega al módulo de comprobantes recibidos usando el menú del portal.
    Flujo confirmado:
      1. Clic en botón "Facturación Electrónica" del menú lateral (title exacto)
      2. Clic en "Comprobantes electrónicos recibidos" del submenú (href exacto)
      3. Esperar que cargue el formulario de consulta (select frmPrincipal:ano)
    """
    log.info("Abriendo menú Facturación Electrónica...")

    # Paso 1: botón del menú lateral — identificado por title exacto
    btn_facturacion = esperar_clickable(driver, By.CSS_SELECTOR,
        "button[title='Facturación Electrónica']"
    )
    btn_facturacion.click()

    # Paso 2: enlace del submenú — identificado por href exacto
    log.info("Seleccionando Comprobantes electrónicos recibidos...")
    enlace_recibidos = esperar_clickable(driver, By.CSS_SELECTOR,
        "a[href*='redireccion=57'][href*='idGrupo=55']"
    )
    enlace_recibidos.click()

    # Paso 3: esperar que cargue el formulario en la nueva pestaña/página
    # El portal puede abrir en nueva pestaña — cambiamos a ella si es necesario
    try:
        esperar(driver, By.ID, "frmPrincipal:ano", timeout=8)
        log.info("Formulario de consulta listo.")
    except TimeoutException:
        # Intentar cambiar a la última pestaña abierta
        if len(driver.window_handles) > 1:
            driver.switch_to.window(driver.window_handles[-1])
            log.info("Cambiado a nueva pestaña del portal.")
        esperar(driver, By.ID, "frmPrincipal:ano")
        log.info("Formulario de consulta listo.")


# ══════════════════════════════════════════════════════════════════════════════
# CONSULTA POR MES/AÑO
# ══════════════════════════════════════════════════════════════════════════════

def consultar_periodo(driver, año, mes):
    """
    Rellena el formulario de búsqueda y lanza la consulta.
    IDs confirmados del portal SRI:
      Año   : frmPrincipal:ano
      Mes   : frmPrincipal:mes
      Día   : frmPrincipal:dia                  → siempre "0" (Todos)
      Tipo  : frmPrincipal:cmbTipoComprobante   → siempre "1" (Factura)
      Botón : frmPrincipal:btnBuscar
    """
    log.info(f"Consultando período: {año}-{mes:02d} | Tipo: Factura | Día: Todos")

    # ── Año ───────────────────────────────────────────────────────────────────
    Select(esperar(driver, By.ID, "frmPrincipal:ano")).select_by_value(str(año))

    # ── Mes ───────────────────────────────────────────────────────────────────
    Select(esperar(driver, By.ID, "frmPrincipal:mes")).select_by_value(str(mes))

    # ── Día → Todos (value="0") ───────────────────────────────────────────────
    Select(esperar(driver, By.ID, "frmPrincipal:dia")).select_by_value("0")

    # ── Tipo de comprobante → Factura (value="1") ─────────────────────────────
    Select(esperar(driver, By.ID, "frmPrincipal:cmbTipoComprobante")).select_by_value("1")

    # ── Botón Consultar ───────────────────────────────────────────────────────
    btn = esperar_clickable(driver, By.ID, "frmPrincipal:btnBuscar")
    driver.execute_script("arguments[0].click();", btn)  # JS click evita intercepts de PrimeFaces

    # ── Esperar que la tabla cargue (primer XML de fila 0 como señal) ─────────
    try:
        WebDriverWait(driver, TIMEOUT).until(
            EC.presence_of_element_located((By.ID, f"{ID_PREFIX}:0:{ID_SUFIJO_XML}"))
        )
        log.info("Tabla de resultados cargada.")
    except TimeoutException:
        log.warning("No hay comprobantes para este período o la tabla tardó demasiado.")


# ══════════════════════════════════════════════════════════════════════════════
# DESCARGA DE COMPROBANTES EN UNA PÁGINA
# ══════════════════════════════════════════════════════════════════════════════

def contar_filas_tabla(driver):
    """
    Cuenta cuántas filas tiene la tabla en la página actual
    buscando el patrón de IDs del SRI: frmPrincipal:tablaCompRecibidos:N:lnkXml
    Retorna el número de filas encontradas.
    """
    idx = 0
    while True:
        elemento_id = f"{ID_PREFIX}:{idx}:{ID_SUFIJO_XML}"
        try:
            driver.find_element(By.ID, elemento_id)
            idx += 1
        except NoSuchElementException:
            break
    return idx


def snapshot_archivos(directorio, extension):
    """Devuelve el conjunto de archivos con esa extensión en el directorio en este momento."""
    return set(glob.glob(str(Path(directorio) / f"*.{extension}")))


def esperar_archivo_nuevo(directorio, extension, archivos_antes, timeout=30):
    """
    Espera a que aparezca cualquier archivo nuevo con la extensión dada,
    comparando contra el snapshot tomado ANTES del clic.
    Ignora .crdownload (descargas en progreso de Chrome).
    Retorna la ruta del archivo nuevo o None si no apareció en el tiempo dado.
    """
    fin = time.time() + timeout
    while time.time() < fin:
        time.sleep(0.4)
        archivos_ahora = set(glob.glob(str(Path(directorio) / f"*.{extension}")))
        nuevos = {
            f for f in archivos_ahora - archivos_antes
            if not f.endswith(".crdownload")
            and not f.endswith(".tmp")
            and not f.endswith(".html")
        }
        if nuevos:
            return nuevos.pop()
    return None


def leer_nombre_comprobante(driver, idx):
    """
    Lee el nombre del comprobante directamente de la tercera columna de la tabla.
    Ejemplo de texto: "Factura  033-110-000290674"
    Devuelve un nombre de archivo seguro, ej: "Factura_033-110-000290674"
    """
    try:
        # La tercera celda (índice 2) de la fila idx contiene tipo + número
        celda = driver.find_element(
            By.XPATH,
            f"//tr[@data-ri='{idx}']/td[3]/div"
        )
        texto = celda.text.strip()
        # Reemplazar espacios múltiples y caracteres no válidos en nombre de archivo
        nombre = " ".join(texto.split())          # colapsar espacios múltiples
        nombre = nombre.replace(" ", "_")          # espacios → guión bajo
        nombre = "".join(c for c in nombre if c.isalnum() or c in "-_.")
        return nombre if nombre else None
    except NoSuchElementException:
        return None


def descargar_pagina_actual(driver, dir_descarga):
    """
    Descarga todos los XML y PDF de la página visible en la tabla,
    renombrando cada par con el número de comprobante extraído del XML.
    Retorna el número de comprobantes procesados.
    """
    try:
        WebDriverWait(driver, TIMEOUT).until(
            EC.presence_of_element_located(
                (By.ID, f"{ID_PREFIX}:0:{ID_SUFIJO_XML}")
            )
        )
    except TimeoutException:
        log.warning("  No hay comprobantes en esta página (tabla vacía).")
        return 0

    total = contar_filas_tabla(driver)
    log.info(f"  → {total} comprobantes en esta página.")

    for idx in range(total):
        etiqueta = f"[{idx + 1}/{total}]"
        punto_de_control()   # Ctrl+Z pausa, Ctrl+C termina

        # ── Leer nombre desde la tabla ANTES de descargar ─────────────────────
        nombre = leer_nombre_comprobante(driver, idx)
        if nombre:
            log.debug(f"    {etiqueta} Nombre leído de tabla: {nombre}")
        else:
            log.warning(f"    {etiqueta} No se pudo leer nombre de la tabla, se usará nombre genérico")

        # ── XML ──────────────────────────────────────────────────────────────
        id_xml = f"{ID_PREFIX}:{idx}:{ID_SUFIJO_XML}"
        ruta_xml = None
        try:
            btn_xml = WebDriverWait(driver, TIMEOUT).until(
                EC.element_to_be_clickable((By.ID, id_xml))
            )
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn_xml)
            snap_xml = snapshot_archivos(dir_descarga, "xml")
            btn_xml.click()
            ruta_xml = esperar_archivo_nuevo(dir_descarga, "xml", snap_xml)
            if ruta_xml:
                destino_xml = Path(dir_descarga) / f"{nombre}.xml" if nombre else Path(ruta_xml)
                Path(ruta_xml).rename(destino_xml)
                log.debug(f"    {etiqueta} XML → {destino_xml.name}")
            else:
                log.warning(f"    {etiqueta} XML no apareció en el directorio")
        except (TimeoutException, NoSuchElementException):
            log.warning(f"    {etiqueta} No se encontró botón XML  (id={id_xml})")
        except Exception as e:
            log.error(f"    {etiqueta} Error al descargar XML: {e}")

        # ── PDF ──────────────────────────────────────────────────────────────
        id_pdf = f"{ID_PREFIX}:{idx}:{ID_SUFIJO_PDF}"
        try:
            btn_pdf = WebDriverWait(driver, TIMEOUT).until(
                EC.element_to_be_clickable((By.ID, id_pdf))
            )
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn_pdf)
            snap_pdf = snapshot_archivos(dir_descarga, "pdf")
            btn_pdf.click()
            ruta_pdf = esperar_archivo_nuevo(dir_descarga, "pdf", snap_pdf)
            if ruta_pdf:
                destino_pdf = Path(dir_descarga) / f"{nombre}.pdf" if nombre else Path(ruta_pdf)
                Path(ruta_pdf).rename(destino_pdf)
                log.debug(f"    {etiqueta} PDF → {destino_pdf.name}")
            else:
                log.warning(f"    {etiqueta} PDF no apareció en el directorio")
            
            # ── Limpiar archivos basura generados por Chrome ──────────────────
            for html in Path(dir_descarga).glob("*.html"):
                html.unlink()
                log.debug(f"    Eliminado archivo basura: {html.name}")
        except (TimeoutException, NoSuchElementException):
            log.warning(f"    {etiqueta} No se encontró botón PDF  (id={id_pdf})")
        except Exception as e:
            log.error(f"    {etiqueta} Error al descargar PDF: {e}")

    return total


# ══════════════════════════════════════════════════════════════════════════════
# PAGINACIÓN
# ══════════════════════════════════════════════════════════════════════════════

def set_resultados_por_pagina(driver):
    """
    Cambia el selector de resultados por pagina al maximo disponible (75).
    ID del paginador: frmPrincipal:tablaCompRecibidos_paginator_bottom
    """
    try:
        sel = Select(WebDriverWait(driver, TIMEOUT).until(
            EC.presence_of_element_located((
                By.CSS_SELECTOR,
                "td#frmPrincipal\\:tablaCompRecibidos_paginator_bottom select.ui-paginator-rpp-options"
            ))
        ))
        opciones_valores = [o.get_attribute("value") for o in sel.options]
        maximo = max(opciones_valores, key=lambda v: int(v))
        if sel.first_selected_option.get_attribute("value") != maximo:
            sel.select_by_value(maximo)
            log.info(f"  Resultados por pagina ajustados a {maximo}.")
            time.sleep(2)
    except Exception as e:
        log.warning(f"  No se pudo ajustar resultados por pagina: {e}")


def hay_pagina_siguiente(driver):
    """
    Devuelve True y hace clic en Siguiente si el paginador SRI lo permite.
    span.ui-paginator-next tiene clase ui-state-disabled en la ultima pagina.
    """
    try:
        btn = driver.find_element(
            By.CSS_SELECTOR,
            "td#frmPrincipal\\:tablaCompRecibidos_paginator_bottom span.ui-paginator-next"
        )
        if "ui-state-disabled" in (btn.get_attribute("class") or ""):
            return False
        btn.click()
        WebDriverWait(driver, TIMEOUT).until(
            EC.presence_of_element_located((By.ID, f"{ID_PREFIX}:0:{ID_SUFIJO_XML}"))
        )
        return True
    except NoSuchElementException:
        return False


# ══════════════════════════════════════════════════════════════════════════════
# FLUJO PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

def descargar_periodo(driver, año, mes, primer_periodo=False):
    """
    Orquesta la consulta y descarga completa para un mes/año.
    - primer_periodo=True  → navega por el menú (primera vez)
    - primer_periodo=False → ya estamos en la página, solo cambia filtros y reconsulta
    """
    dir_descarga = directorio_mes(año, mes)

    # Redirigir descargas al directorio del mes actual
    driver.execute_cdp_cmd("Page.setDownloadBehavior", {
        "behavior": "allow",
        "downloadPath": dir_descarga,
    })

    if primer_periodo:
        navegar_comprobantes_recibidos(driver)
    else:
        # Ya estamos en la página — solo esperar que el formulario esté activo
        try:
            esperar(driver, By.ID, "frmPrincipal:ano", timeout=5)
        except TimeoutException:
            # Si por alguna razón perdimos la página, navegamos de nuevo
            log.warning("Formulario no encontrado, navegando de nuevo...")
            navegar_comprobantes_recibidos(driver)

    consultar_periodo(driver, año, mes)
    set_resultados_por_pagina(driver)  # maximizar filas por página (75)

    pagina = 1
    total_descargados = 0

    while True:
        log.info(f"  Página {pagina} — descargando...")
        n = descargar_pagina_actual(driver, dir_descarga)
        total_descargados += n

        if n == 0 or not hay_pagina_siguiente(driver):
            break
        pagina += 1

    log.info(f"Período {año}-{mes:02d}: {total_descargados} comprobantes en {pagina} página(s).")
    return total_descargados

def generar_periodos(mes_ini, año_ini, mes_fin, año_fin):
    """Genera lista de (año, mes) entre los períodos dados."""
    periodos = []
    año, mes = año_ini, mes_ini
    while (año, mes) <= (año_fin, mes_fin):
        periodos.append((año, mes))
        mes += 1
        if mes > 12:
            mes = 1
            año += 1
    return periodos


def main():
    registrar_señales()
    log.info("═" * 60)
    log.info("SRI — Descarga automática de comprobantes electrónicos")
    log.info(f"Períodos: {AÑO_INICIO}-{MES_INICIO:02d} → {AÑO_FIN}-{MES_FIN:02d}")
    log.info(f"Directorio de salida: {DIRECTORIO_DESCARGA}")
    log.info("═" * 60)

    # Inicializar con directorio base (se actualiza por período)
    Path(DIRECTORIO_DESCARGA).mkdir(parents=True, exist_ok=True)
    driver = configurar_driver(DIRECTORIO_DESCARGA)

    try:
        hacer_login(driver)
        periodos = generar_periodos(MES_INICIO, AÑO_INICIO, MES_FIN, AÑO_FIN)

        resumen = {}
        for i, (año, mes) in enumerate(periodos):
            punto_de_control()   # Ctrl+Z pausa, Ctrl+C termina
            try:
                n = descargar_periodo(driver, año, mes, primer_periodo=(i == 0))
                resumen[f"{año}-{mes:02d}"] = n
            except Exception as e:
                log.error(f"Error en período {año}-{mes:02d}: {e}")
                resumen[f"{año}-{mes:02d}"] = "ERROR"

        # Resumen final
        log.info("═" * 60)
        log.info("RESUMEN DE DESCARGA")
        for periodo, n in resumen.items():
            log.info(f"  {periodo}: {n} comprobante(s)")
        log.info("═" * 60)

    except SystemExit as e:
        log.info(str(e))
    finally:
        driver.quit()
        log.info("Navegador cerrado. Proceso finalizado.")


if __name__ == "__main__":
    main()
