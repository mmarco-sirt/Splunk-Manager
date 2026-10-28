"""
Convenciones de SIRT para los casos de uso de Splunk.

Alertas en producción: «CU-<TEC>-<ID>-<MITRE> <Nombre>». En pruebas llevan delante el prefijo «TEST - »
(p. ej. «TEST - CU-CA-AUTH-1152-T1078 Geographically Improbable Access»).

Aquí el prefijo TEST se reconoce de forma tolerante (TEST-CU-…, Test - CU-…) para que ninguna alerta de pruebas se quede
fuera de los listados, backups y comparaciones.

La app propia de SIRT es SIRT_APP; si en algún cliente se llama de otra forma, se indica en su campo "sirt_app" de
config.py (también desde la web, en Clientes).
"""
import re

CU_PREFIX = "CU-"
TEST_PREFIX = "TEST - "
_TEST_PREFIX = re.compile(r"TEST\s*-\s*", re.IGNORECASE)

SIRT_APP = "sirt_soc_app_for_splunk"
SIRT_APP_LABEL = "SIRT SOC App for Splunk"


def sirt_app(client=None):
    """Nombre (id) de la app de SIRT en un cliente: su campo 'sirt_app' o, si no lo tiene, SIRT_APP."""
    return str((client or {}).get("sirt_app") or "").strip() or SIRT_APP


def strip_test_prefix(name):
    """'TEST - CU-AD-001 X' -> 'CU-AD-001 X'. Si no es la versión de pruebas de un caso de uso, devuelve el nombre igual."""
    name = str(name or "")
    match = _TEST_PREFIX.match(name)
    if match and name.startswith(CU_PREFIX, match.end()):
        return name[match.end():]
    return name


def is_test_alert(name):
    """Versión de pruebas de un caso de uso: «TEST - CU-…»."""
    name = str(name or "")
    return strip_test_prefix(name) != name


def is_cu_alert(name, include_test=True):
    """Caso de uso «CU-…»; con include_test, también su versión de pruebas «TEST - CU-…»."""
    name = str(name or "")
    return name.startswith(CU_PREFIX) or (include_test and is_test_alert(name))


def is_counterpart(name, other):
    """True si una es la versión TEST y la otra la de producción del mismo caso de uso ('CU-X' y 'TEST - CU-X')."""
    return (is_cu_alert(name) and is_test_alert(name) != is_test_alert(other)
            and strip_test_prefix(name) == strip_test_prefix(other))
