# SRI Ecuador — Descarga automática de comprobantes electrónicos

Automatiza la descarga de documentos electrónicos recibidos desde el portal del SRI, organizándolos por mes en XML y PDF.


## ¿Qué hace?

1. Abre Chrome y navega al portal SRI en Línea
2. Inicia sesión con tu RUC y contraseña
3. Abre el menú **Facturación Electrónica → Comprobantes electrónicos recibidos**
4. Por cada mes del rango configurado:
   - Selecciona año, mes (automático), día = Todos, tipo = Factura
   - Ajusta la tabla a 75 resultados por página
   - Lee el nombre de cada comprobante directamente desde la tabla (ej: `Factura 001-001-000000001`)
   - Descarga el XML y PDF de cada fila y los renombra automáticamente
   - Avanza página por página hasta agotar los resultados
5. Guarda todo organizado por carpetas `YYYY-MM`
6. Genera un log con el resumen de descargas

Resultado:

```
~/sri_comprobantes/
├── 2026-01/
│   ├── Factura_001-001-000000001.xml
│   ├── Factura_001-001-000000001.pdf
│   ├── Factura_001-001-000000002.xml
│   ├── Factura_001-001-000000002.pdf
│   └── ...
├── 2026-02/
│   └── ...
```

## Requisitos

- Linux
- Python 3.10+
- Google Chrome

## Instalación

```bash
# 1. Crear entorno virtual
python3 -m venv venv
source venv/bin/activate

# 2. Instalar dependencias
pip install selenium webdriver-manager
```

`webdriver-manager` descarga automáticamente el ChromeDriver compatible con tu versión de Chrome. No necesitas instalarlo manualmente.

## Configuración

Edita la sección `CONFIG` al inicio de `descargar_comprobantes_sri.py`:

| Variable | Ejemplo                    | Descripción |
|---|----------------------------|---|
| `RUC` | `"0999999999001"`          | Tu RUC o cédula |
| `CLAVE` | `"tu_clave"`               | Tu contraseña del portal SRI |
| `MES_INICIO` / `AÑO_INICIO` | `1` / `2025`               | Primer mes del rango |
| `MES_FIN` / `AÑO_FIN` | `12` / `2025`              | Último mes del rango |
| `DIRECTORIO_DESCARGA` | `"~/sri_comprobantes"`     | Carpeta raíz de descargas |
| `BROWSER_PATH` | `"/usr/bin/google-chrome"` | Ruta al ejecutable del navegador |
| `TIMEOUT` | `20`                       | Segundos máximos de espera por elemento |
| `PAUSA_ENTRE_DESCARGAS` | `1.5`                      | Pausa en segundos entre cada descarga |

## Ejecución

```bash
source venv/bin/activate
export SRI_RUC="0999999999001"
export SRI_CLAVE="contraseña_SRI"
python3 descargar_comprobantes_sri.py
```

**Probar con un solo mes antes de lanzar un rango largo:**

```python
# En la sección CONFIG:
MES_INICIO = 3
AÑO_INICIO = 2026
MES_FIN    = 3
AÑO_FIN    = 2026
```

**Modo headless** (sin ventana de Chrome): descomenta esta línea en `configurar_driver()`:

```python
opciones.add_argument("--headless=new")
```

## Control del teclado

Durante la ejecución puedes intervenir en cualquier momento:

| Teclas | Efecto                                                |
|---|-------------------------------------------------------|
| `Ctrl+Z` | Pausa la ejecución. Vuelve a presionar para reanudar. |
| `Ctrl+C` | Termina la ejecución y cierra Chrome.                 |

El script nunca interrumpe una descarga a mitad — siempre espera terminar el comprobante en curso antes de pausar o salir.
