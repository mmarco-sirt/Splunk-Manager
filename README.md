# Splunk Manager

Herramienta para administrar varias plataformas Splunk (backup y restore de alertas, macros,
usuarios, roles, dashboards y reportes, también en modo batch sobre varios clientes).

## Estructura

```
main_cli.py              Menú de consola
main_gui.py              Interfaz gráfica (Tkinter)
main_web.py              Interfaz web (Flask), ver más abajo
core/
  paths.py               Rutas base (config.py y backups/, junto al script o al .exe)
  connection.py          Selección de cliente y conexión a Splunk
  clients.py             Lectura/escritura de clientes en config.py
  backup_files.py        Listado y selección de archivos de backup
options/
  alerts/                Backup / restore / listado de alertas (incl. Enterprise Security)
  macros/  users/  roles/  dashboards/  reports/
  batch/                 Operaciones sobre varios clientes o varios archivos
web/
  app.py                 API JSON y página web
  services.py            Conexiones reutilizables a cada cliente
  explorer.py            Consultas en vivo (alertas, macros, usuarios, índices, búsquedas SPL...)
  operations.py          Backup/restore desde la web usando las funciones de options/
  jobs.py                Tareas en segundo plano con su log
  templates/ static/     Interfaz (HTML/CSS/JS sin dependencias externas)
backups/<tipo>/          Backups generados (no se versiona)
config.py                Clientes y credenciales (no se versiona)
```

## Puesta en marcha

1. Instalar dependencias: `pip install -r requirements.txt`
2. Copiar `config.py.example` como `config.py` y rellenar los clientes
   (también se pueden añadir desde la propia aplicación: "Gestionar Clientes").
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
- **Macros, Dashboards, Usuarios, Roles**: listado, detalle, backup y restore.
- **Índices / Apps**: eventos, tamaño, retención y archivado (en Splunk Cloud, datos reales de los indexers),
  versiones y estado.
- **Ingesta y licencia**: GB/día de los últimos 7, 30 o 90 días según `license_usage.log`, por índice y sourcetype,
  con su tendencia.
- **Backups**: archivos por tipo, visor del contenido, descarga, importación de JSON, borrado y restore
  (también de solo algunos elementos de un archivo), y **historial de versiones** con comparación y restore de
  una versión anterior.
- **Operaciones masivas**: un mismo backup o restore en varios clientes a la vez.
- **Clientes**: alta, edición, borrado y prueba de conexión (las contraseñas nunca se envían al navegador).
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

El `.exe` busca `config.py` y `backups/` en su misma carpeta.
