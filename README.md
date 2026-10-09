# Splunk Manager

Herramienta para administrar varias plataformas Splunk: backup y restore de alertas, macros, usuarios, roles,
dashboards y reportes (también en modo batch sobre varios clientes), edición de alertas, comparación de
clientes e informes en Excel/HTML/PDF.

## Estructura

```
main_cli.py              Menú de consola
main_gui.py              Interfaz gráfica (Tkinter)
main_web.py              Interfaz web (Flask), ver más abajo
core/
  paths.py               Rutas base (config.py, backups/ e informes/, junto al script o al .exe)
  connection.py          Selección de cliente y conexión a Splunk (método preferido + respaldo)
  clients.py             Lectura/escritura de clientes en config.py (credenciales cifradas)
  vault.py               Cifrado de credenciales con DPAPI de Windows
  backup_files.py        Listado y selección de archivos de backup
options/
  alerts/                Backup / restore / listado de alertas (incl. Enterprise Security)
  macros/  users/  roles/  dashboards/  reports/
  batch/                 Operaciones sobre varios clientes o varios archivos
web/
  app.py                 API JSON y página web
  services.py            Conexiones reutilizables a cada cliente
  explorer.py            Consultas en vivo (alertas, macros, usuarios, índices, búsquedas SPL...)
  alert_editor.py        Edición de alertas y reportes (también en bloque)
  compare.py             Comparación de clientes y copia de objetos entre ellos
  reports.py, xlsx.py    Informes en Excel, HTML y PDF
  operations.py          Backup/restore desde la web usando las funciones de options/
  jobs.py                Tareas en segundo plano con su log
  templates/ static/     Interfaz (HTML/CSS/JS sin dependencias externas) y plantilla de los informes
backups/<tipo>/          Backups generados (no se versiona)
informes/                Informes generados (no se versiona)
config.py                Clientes y credenciales cifradas (no se versiona)
```

## Puesta en marcha

1. Instalar dependencias: `pip install -r requirements.txt`
2. Añadir los clientes desde la propia aplicación (web: «Clientes»; CLI/escritorio: «Gestionar Clientes»).
   También se puede copiar `config.py.example` como `config.py` y rellenarlo a mano: las contraseñas y tokens que
   estén en claro se cifran automáticamente la primera vez que se arranca cualquiera de las tres interfaces.
3. Ejecutar:
   - Consola: `python main_cli.py`
   - Gráfico: `python main_gui.py`
   - Web: `python main_web.py` (abre el navegador en http://127.0.0.1:8765)

## Interfaz web

`python main_web.py` arranca un servidor local y abre el navegador. Usa el mismo `config.py` y la misma
carpeta `backups/` que el CLI y la aplicación de escritorio, así que se pueden usar indistintamente.

Secciones:
- **Resumen**: versión y datos del servidor, recuento de objetos, alertas más disparadas e índices por tamaño.
- **Salud de clientes**: todos los clientes de un vistazo (conexión, alertas, planificador y ingesta de las
  últimas 24 h, y fuentes que han dejado de enviar datos), con el umbral configurable y fuentes que se pueden ignorar.
- **Búsqueda**: búsquedas SPL con rango de tiempo, progreso, cancelación y exportación a CSV/JSON, en el cliente
  activo o en **varios clientes a la vez** (resultados unidos con una columna `cliente`, p. ej. para buscar un IOC).
- **Alertas / Reportes**: listado en vivo con filtros (CU-, Enterprise Security, habilitadas...), detalle con el SPL,
  habilitar/deshabilitar, ejecutar ahora, backup (todas, por tecnología, por texto, ES o selección) y restore.
  **Edición**: SPL, descripción, programación (cron con ayuda), ventana, condición de disparo, umbral, severidad,
  supresión, acciones y email, y un editor «Avanzado» con todos los parámetros. Antes de guardar se muestra el
  resumen de cambios. También se pueden **modificar en bloque** varias seleccionadas. Antes de cada cambio se guarda
  la configuración anterior en `backups/<alertas|reportes>/…antes_de_editar_<host>_backup.json` (con historial), así
  que se puede deshacer restaurando ese archivo (o solo algunos elementos).
- **Macros, Dashboards, Usuarios, Roles**: listado, detalle, backup y restore.
- **Índices / Apps**: eventos, tamaño, retención y archivado (en Splunk Cloud, datos reales de los indexers),
  versiones y estado.
- **Ingesta y licencia**: GB/día de los últimos 7, 30 o 90 días según `license_usage.log`, por índice y sourcetype,
  con su tendencia.
- **Backups**: archivos por tipo, visor del contenido, descarga, importación de JSON, borrado y restore
  (también de solo algunos elementos de un archivo), y **historial de versiones** con comparación y restore de
  una versión anterior.
- **Operaciones masivas**: un mismo backup o restore en varios clientes a la vez.
- **Comparar clientes**: un cliente de referencia frente a uno o varios clientes (alertas, reportes, macros,
  dashboards, roles, usuarios, índices y apps): qué falta, qué sobra y qué es distinto, con las diferencias campo a
  campo. Las alertas, reportes, macros, dashboards y roles se pueden **copiar** de un cliente a otros; antes de
  sobrescribir se guarda lo que había en `backups/<tipo>/…antes_de_copiar_<host>_backup.json`.
- **Informes**: informe de uno o varios clientes con salud, ingesta, inventario de alertas, índices y usuarios/roles,
  en **Excel**, **HTML** (para ver o imprimir) y **PDF** (se genera con Microsoft Edge en modo headless). Se guardan en
  `informes/`. También desde «Salud de clientes» → «Exportar informe…».
- **Clientes**: alta, edición, borrado y prueba de cada credencial antes de guardar (las contraseñas y tokens nunca
  se envían al navegador).
- **Actividad**: cada backup/restore se ejecuta como tarea en segundo plano con su log en vivo.

Opciones: `--port 8765`, `--host 127.0.0.1`, `--no-browser`. Por defecto solo escucha en este equipo.
Para abrirlo a la red (`--host 0.0.0.0`) es obligatorio definir una contraseña de acceso en la variable de
entorno `SPLUNK_MANAGER_PASSWORD` (el navegador la pedirá; el usuario puede ser cualquiera), ya que la
aplicación tiene acceso a las credenciales de todos los clientes.

## Historial de backups

Cada backup (desde la web, el CLI o la aplicación de escritorio) guarda además una copia con fecha en
`backups/<tipo>/historial/<nombre del archivo>/AAAAMMDD-HHMMSS.json`, solo si ha cambiado respecto a la versión
anterior, y se conservan las 30 más recientes (`MAX_VERSIONS` en `core/backup_files.py`). Al comparar versiones se
ignoran los campos que Splunk cambia solo (`next_scheduled_time`, `triggered_alert_count`). El archivo "actual"
sigue teniendo el mismo nombre de siempre, así que los restores del CLI y la GUI no cambian.

## Credenciales

Cada cliente puede tener **usuario/contraseña, token o ambos**. Con los dos se elige el **método preferido** y, si Splunk
rechaza esas credenciales, se prueba automáticamente el otro (la web avisa de que se conectó con el método alternativo).
Con un token se comprueba al conectar que es válido.

Las contraseñas y tokens se guardan **cifrados** en `config.py` (`"password": "dpapi:…"`) con **DPAPI de Windows**: solo
los puede descifrar el mismo usuario de Windows (en un dominio con perfil móvil, también en otro equipo con ese usuario).
Si se copia `config.py` a otro usuario o equipo, la aplicación avisa de qué credenciales hay que volver a introducir.

Los `config.py` antiguos se migran solos al arrancar cualquiera de las tres interfaces: se cifran las credenciales en
claro y la lista antigua `clientes_token` se une a `clientes` (mismo nombre o mismo host = mismo cliente). Formato:

```python
clientes = [
    {
        "name": "CLIENTE",
        "host": "cliente.splunkcloud.com",
        "username": "usuario",
        "password": "dpapi:AQAAANCMnd8B…",     # cifrado automáticamente
        "splunkToken": "dpapi:AQAAANCMnd8B…",  # opcional
        "auth": "token",                        # opcional: método preferido si hay los dos
    },
]
```

## Restore de usuarios

Los archivos de `backups/users/` pueden ser:
- Plantillas manuales: `username`, `password`, `roles`, `email`, `realname`, `force-change-pass` (opcional).
- Salida de "Backup Users": sin contraseña (Splunk no la expone). Los usuarios existentes se
  actualizan; para los nuevos se pide una contraseña inicial y se fuerza su cambio en el primer login
  (en la web se indica en el diálogo de restore).

## Generar ejecutables

```
python -m PyInstaller Splunk_Manager.spec       # GUI  -> dist/Splunk_Manager.exe
python -m PyInstaller Splunk_Manager_CLI.spec   # CLI  -> dist/Splunk_Manager_CLI.exe
python -m PyInstaller Splunk_Manager_Web.spec   # Web  -> dist/Splunk_Manager_Web.exe
```

El `.exe` busca `config.py`, `backups/` e `informes/` en su misma carpeta. `config.py` nunca se incluye dentro del
ejecutable (`excludes=['config']` en los .spec).
