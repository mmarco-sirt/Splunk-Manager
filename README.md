# Splunk Manager

Herramienta para administrar varias plataformas Splunk: backup y restore de alertas, macros, usuarios, roles,
dashboards y reportes (también en modo batch sobre varios clientes), edición de alertas (también en varios clientes a
la vez), dependencias de cada alerta (macros y lookups), inventario de lookups, análisis de alertas ruidosas y muertas,
comparación de clientes, validación de versiones de la app de SIRT antes de desplegarlas e informes en Excel/HTML/PDF.
El CLI se puede programar con el Programador de tareas de Windows para hacer backups e informes automáticos.

## Estructura

```
main_cli.py              Consola: menú interactivo o, con argumentos, modo no interactivo (tareas programadas)
main_web.py              Interfaz web (Flask), ver más abajo
core/
  paths.py               Rutas base (config.py, backups/ e informes/, junto al script o al .exe)
  connection.py          Selección de cliente y conexión a Splunk (método preferido + respaldo)
  clients.py             Lectura/escritura de clientes en config.py (credenciales cifradas)
  vault.py               Cifrado de credenciales con DPAPI de Windows
  backup_files.py        Listado y selección de archivos de backup
  conventions.py         Convenciones de SIRT: casos de uso CU- / TEST - CU- y la app de SIRT
  knowledge.py           Saved searches y macros de todas las apps y usuarios, y cómo localizar cada una
  spl.py                 Análisis estático de SPL: macros, lookups, comandos que escriben datos y expansión de macros
  dependencies.py        Dependencias de una búsqueda (macros anidadas y lookups) y si se ven desde su app
  splunk_conf.py         Lectura de .conf/.meta como Splunk (stanzas, continuaciones, duplicados) y de su codificación
options/
  alerts/                Backup / restore / listado / habilitar y deshabilitar alertas (incl. Enterprise Security)
  macros/  users/  roles/  dashboards/  reports/
  batch/                 Operaciones sobre varios clientes o varios archivos, y modo no interactivo del CLI
web/
  app.py                 API JSON y página web
  services.py            Conexiones reutilizables a cada cliente
  explorer.py            Consultas en vivo (alertas, macros, usuarios, índices, búsquedas SPL...)
  lookups.py             Lookups (definiciones, archivos y automáticos), dónde se usan y vista previa
  dependencies.py        Dependencias de una saved search en un cliente
  app_packages.py        Validar app: paquetes subidos (lectura segura, historial y resultados guardados)
  app_diff.py            Validar app: cambios entre versiones o frente a un cliente
  app_validation.py      Validar app: comprobaciones del paquete y contra un cliente, y ejecución de búsquedas
  app_report.py          Validar app: informe en Excel, HTML y PDF
  alert_editor.py        Edición de alertas y reportes (también en bloque)
  multi_edit.py          Edición de alertas en varios clientes a la vez (vista previa y backup)
  alert_analysis.py      Análisis de alertas: ruidosas, muertas, planificador y tendencia
  compare.py             Comparación de clientes y copia de objetos entre ellos
  reports.py, xlsx.py    Informes en Excel, HTML y PDF
  operations.py          Backup/restore desde la web usando las funciones de options/
  jobs.py                Tareas en segundo plano con su log
  templates/ static/     Interfaz (HTML/CSS/JS sin dependencias externas) y plantilla de los informes
tests/                   Tests de las funciones puras (python -m unittest discover -s tests -t .)
backups/<tipo>/          Backups generados (no se versiona)
informes/                Informes generados (no se versiona)
apps_subidas/            Versiones de la app subidas en «Validar app» y sus resultados (no se versiona)
logs/                    Logs del modo no interactivo del CLI (no se versiona)
config.py                Clientes y credenciales cifradas (no se versiona)
```

## Puesta en marcha

1. Instalar dependencias: `pip install -r requirements.txt`
2. Añadir los clientes desde la propia aplicación (web: «Clientes»; CLI: «Gestionar Clientes»).
   También se puede copiar `config.py.example` como `config.py` y rellenarlo a mano: las contraseñas y tokens que
   estén en claro se cifran automáticamente la primera vez que se arranca la web o el CLI.
3. Ejecutar:
   - Web: `python main_web.py` (abre el navegador en http://127.0.0.1:8765)
   - Consola: `python main_cli.py` (menú) o `python main_cli.py --help` (modo no interactivo, ver «Tareas programadas»)

## Interfaz web

`python main_web.py` arranca un servidor local y abre el navegador. Usa el mismo `config.py` y la misma
carpeta `backups/` que el CLI, así que se pueden usar indistintamente.

Secciones:
- **Resumen**: versión y datos del servidor, recuento de objetos, alertas más disparadas e índices por tamaño.
- **Salud de clientes**: todos los clientes de un vistazo (conexión, alertas, planificador y ingesta de las
  últimas 24 h, y fuentes que han dejado de enviar datos), con el umbral configurable y fuentes que se pueden ignorar.
- **Búsqueda**: búsquedas SPL con rango de tiempo, progreso, cancelación y exportación a CSV/JSON, en el cliente
  activo o en **varios clientes a la vez** (resultados unidos con una columna `cliente`, p. ej. para buscar un IOC).
- **Alertas / Reportes**: listado en vivo de todas las apps y usuarios (también las privadas y las compartidas solo en
  su app, con su propietario y compartición) y filtros combinables con su etiqueta: **Tipo** (CU-, TEST, Enterprise
  Security, otras), **Estado** y **App** (todas o solo la app de SIRT). Detalle con el SPL y sus **dependencias**,
  habilitar/deshabilitar (una o varias seleccionadas, guardando antes su configuración), ejecutar ahora, backup (todas,
  por tecnología, por texto, ES o selección) y restore. Las alertas de pruebas `TEST - CU-…` llevan la etiqueta TEST (ver
  «Alertas TEST y objetos de otros usuarios»).
  **Dependencias**: las macros que necesita la búsqueda (también las que llaman otras macros, sangradas debajo) y los
  lookups que usa (`lookup`, `inputlookup`, `outputlookup`), cada uno con su estado: OK, no existe (y, si la hay, la
  macro con otro nº de argumentos), deshabilitado, no visible desde la app de la alerta (solo está en otra app o es
  privado de otro usuario) o definición de lookup cuyo CSV falta. Se resuelven como Splunk: desde la app y el propietario
  de la alerta, y si hay varias con el mismo nombre se usa la privada del propietario, luego la de su app y por último
  una global; se avisa si una tapa a otra.
  **Edición**: SPL, descripción, programación (cron con ayuda), ventana, condición de disparo, umbral, severidad,
  supresión, acciones y email, y un editor «Avanzado» con todos los parámetros. Antes de guardar se muestra el
  resumen de cambios. También se pueden **modificar en bloque** varias seleccionadas. Antes de cada cambio se guarda
  la configuración anterior en `backups/<alertas|reportes>/…antes_de_editar_<host>_backup.json` (con historial), así
  que se puede deshacer restaurando ese archivo (o solo algunos elementos).
- **Análisis de alertas**: a partir del log del planificador (`index=_internal sourcetype=scheduler`, 7, 14 o 30 días):
  - **Ruidosas**: disparan de media 10 o más veces al día (umbral configurable) o en casi todas sus ejecuciones, con
    sus resultados, si tienen supresión (throttle) y su tendencia.
  - **Muertas**: habilitadas pero que nunca disparan, no tienen acciones, no se ejecutan aunque su cron dice que
    deberían, el planificador las salta siempre o fallan siempre (o están habilitadas sin programar).
  - **Planificador**: se saltan a menudo (con el motivo), tienen errores o se ejecutan menos de lo que indica su cron.
  - **Tendencia**: disparos por día de cada alerta y comparación de la segunda mitad del periodo con la primera.

  Desde cada lista se pueden deshabilitar, modificar en bloque o editar en varios clientes las seleccionadas.
- **Edición en varios clientes**: una o varias alertas (p. ej. `CU-AD-001`) en todos los clientes que la tienen.
  Se busca en qué clientes existe, se elige qué cambiar (el mismo valor en todos, p. ej. el cron, o **buscar y
  reemplazar** un texto en el SPL de cada cliente, que conserva el resto de su búsqueda) y una **vista previa** muestra
  campo a campo qué cambia en cada cliente. Al aplicar se guarda antes la configuración de cada cliente en su archivo
  «antes_de_editar» y solo se modifica lo que no haya cambiado desde la vista previa. También sirve para habilitar o
  deshabilitar una alerta en todos los clientes. `CU-X` y `TEST - CU-X` son alertas distintas: al buscar una se indica en
  qué clientes existe la otra versión y se puede añadir (sin marcar por defecto).
- **Macros**: listado de todas las apps y usuarios con filtros (compartición, estado y app SIRT), detalle con la
  definición y **dónde se usa** (alertas, reportes y otras macros), **edición** (definición, argumentos, eval, descripción
  y validación, con revisión de cambios) y **habilitar o deshabilitar** una o varias seleccionadas. Antes de cada cambio
  se guarda la configuración anterior en `backups/macros/macros_antes_de_editar_<host>_backup.json` (con historial), así
  que se puede deshacer restaurándolo. Los backups de macros incluyen también eval, estado y descripción, y el restore
  los aplica.
- **Lookups**: definiciones (CSV, KV Store, externos y geoespaciales), archivos de lookup y lookups automáticos de todas
  las apps y usuarios, con filtros (tipo, con problemas, app SIRT). Se avisa de las definiciones cuyo CSV no existe o no
  se ve desde su app, de las del KV Store sin colección y de los automáticos sin definición. El detalle muestra su
  configuración, **dónde se usa** (alertas, reportes, macros y lookups automáticos; un CSV también a través de las
  definiciones que apuntan a él) y, a petición, el **contenido**: las 100 primeras filas y el total, con
  `| inputlookup` en la app del lookup (solo lectura).
- **Dashboards, Usuarios, Roles**: listado, detalle, backup y restore.
- **Índices / Apps**: eventos, tamaño, retención y archivado (en Splunk Cloud, datos reales de los indexers),
  versiones y estado.
- **Ingesta y licencia**: GB/día de los últimos 7, 30 o 90 días según `license_usage.log`, por índice y sourcetype,
  con su tendencia. Muestra también los entitlements de la suscripción de Splunk Cloud (licencia de ingesta, DDAS,
  DDAA y restauración DDAA, con la misma búsqueda que la Cloud Monitoring Console; requiere el rol `sc_admin`), el % de
  uso de la licencia y los días que la superan. Los informes incluyen los mismos datos.
- **Backups**: archivos por tipo, visor del contenido, descarga, importación de JSON, borrado y restore
  (también de solo algunos elementos de un archivo), y **historial de versiones** con comparación y restore de
  una versión anterior.
- **Operaciones masivas**: un mismo backup o restore en varios clientes a la vez.
- **Comparar clientes**: un cliente de referencia frente a uno o varios clientes (alertas, reportes, macros,
  dashboards, roles, usuarios, índices y apps): qué falta, qué sobra y qué es distinto, con las diferencias campo a
  campo. Las alertas, reportes, macros, dashboards y roles se pueden **copiar** de un cliente a otros; antes de
  sobrescribir se guarda lo que había en `backups/<tipo>/…antes_de_copiar_<host>_backup.json`. En alertas y reportes,
  **Emparejar TEST y producción** pone `TEST - CU-X` y `CU-X` en la misma fila para ver en qué fase está cada cliente
  (en cada uno se compara la misma fase que en la referencia, o la otra si no la tiene); la copia usa el nombre real.
- **Validar app**: validación de una versión de la app de SIRT antes de desplegarla (ver «Validar app»).
- **Informes**: informe de uno o varios clientes con salud, ingesta, inventario y análisis de alertas, índices y usuarios/roles,
  en **Excel**, **HTML** (para ver o imprimir) y **PDF** (se genera con Microsoft Edge en modo headless). Se guardan en
  `informes/`. También desde «Salud de clientes» → «Exportar informe…».
- **Clientes**: alta, edición, borrado y prueba de cada credencial antes de guardar (las contraseñas y tokens nunca
  se envían al navegador). Cada cliente puede indicar su **App SIRT** si allí no se llama `sirt_soc_app_for_splunk`.
- **Actividad**: cada backup/restore se ejecuta como tarea en segundo plano con su log en vivo. Las tareas largas de
  «Validar app» muestran su progreso (n de m) y se pueden **cancelar**: paran al terminar la búsqueda en curso.

Opciones: `--port 8765`, `--host 127.0.0.1`, `--no-browser`. Por defecto solo escucha en este equipo.
Para abrirlo a la red (`--host 0.0.0.0`) es obligatorio definir una contraseña de acceso en la variable de
entorno `SPLUNK_MANAGER_PASSWORD` (el navegador la pedirá; el usuario puede ser cualquiera), ya que la
aplicación tiene acceso a las credenciales de todos los clientes.

## Alertas TEST y objetos de otros usuarios

Convención de nombres: en producción `CU-<TEC>-<ID>-<MITRE> <Nombre>` y en pruebas el mismo nombre con el prefijo
`TEST - ` (p. ej. `TEST - CU-CA-AUTH-1152-T1078 Geographically Improbable Access`). La lógica está en
`core/conventions.py` (`is_cu_alert`, `is_test_alert`, `strip_test_prefix`); el prefijo se reconoce aunque esté mal
escrito (`TEST-CU-…`, `Test - CU-…`).

- Los backups de alertas «CU-\*» (web y `--backup alertas` del CLI) incluyen también las `TEST - CU-*`.
- El listado de Alertas, el Análisis de alertas y Comparar distinguen en el filtro **Tipo** los CU- de producción y
  las TEST.
- En Salud de clientes, «casos de uso CU- deshabilitados» cuenta solo los de producción (es normal que una TEST esté
  deshabilitada).

La app propia de SIRT es `sirt_soc_app_for_splunk` (`SIRT_APP` en `core/conventions.py`). Si en algún cliente se llama
de otra forma, se indica en su campo **App SIRT** (en Clientes, o `"sirt_app"` en `config.py`). La usa el filtro
«App SIRT» de Alertas, Reportes, Macros y Lookups.

Las saved searches y las macros se leen de **todas las apps y usuarios** (`/servicesNS/-/-/`): con `/services/` Splunk
no devuelve los objetos privados de otros usuarios ni los compartidos solo dentro de otra app, y por eso no se veían
algunas TEST. Puede haber varios con el mismo nombre (p. ej. el compartido y la copia privada de un usuario), así que cada
uno se identifica por su nombre, app, propietario y compartición (`core/knowledge.py`):

- Los backups de alertas, reportes y macros guardan de cada elemento su `acl` (app, propietario y compartición). Al
  restaurar se actualiza ese mismo objeto, en su app; si ya no está, uno compartido con el mismo nombre si el del
  backup lo era, pero nunca la copia privada de otro usuario (se crea uno nuevo). Los backups antiguos, sin `acl`, se
  restauran como antes.
- «Compartición tras restaurar» (alertas y reportes) tiene una opción nueva, **Mantener la que tenían** (por defecto en
  la web): las que ya existen conservan la suya y las nuevas usan la del backup (o global en los backups antiguos). El
  restore del CLI sigue aplicando global.
- El restore de macros actualiza cada macro en su propia app. Antes la actualización iba al namespace `nobody/system`
  (el SDK toma `sharing` como namespace), así que no cambiaba la macro de la app. Con los backups antiguos las macros
  existentes se siguen haciendo globales y las nuevas se crean como antes.
- En Comparar, los privados aparecen como `nombre [privada de usuario]`.

## Validar app

Sección **Gestión → Validar app**, para comprobar una versión de la app de SIRT antes de desplegarla.

**Subir.** Se admite `.tgz`, `.tar.gz`, `.spl` y `.zip` (como mucho 100 MB, 200 MB descomprimido y 10.000 archivos).
Cada versión se guarda tal cual en `apps_subidas/<app>/<fecha>_<versión>/` y queda en el historial (las 30 últimas de
cada app; subir otra vez el mismo archivo no lo duplica). Sus archivos nunca se extraen a disco: se leen en memoria
rechazando rutas absolutas o con `..`, enlaces, dispositivos y nombres reservados de Windows. La versión se lee de
`default/app.conf` (`[launcher] version`, y `[id]`).

**Validación (pestaña Validación).** Sin consultar ningún Splunk:
- Codificación: UTF-8 válido, sin BOM (error en .conf, .meta y CSV), sin tildes o eñes rotas («Ã³») y sin finales de
  línea mezclados.
- Sintaxis de los `.conf` y `.meta` (`core/splunk_conf.py`): stanzas bien cerradas, líneas sin `clave = valor`,
  continuaciones `\` (también la `\` seguida de espacios, que corta la línea) y claves repetidas.
- `app.conf` (versión X.Y.Z, que coincidan `[id]` y `[launcher]`, id y carpeta, nombre visible) y `local/` dentro del
  paquete (AppInspect lo rechaza en Splunk Cloud).
- Nombres de las saved searches: `[TEST - ]CU-<TEC>-<ID>-<MITRE> <Nombre>`, con TEC en mayúsculas (uno o varios bloques
  con guiones), ID numérico y técnica `T1234` o `T1234.567`. En las alertas es un error; en los informes, un aviso.
- Campos obligatorios de cada alerta: descripción, cron (válido y con `enableSched = 1`), ventana
  (`dispatch.earliest_time`), condición de disparo, severidad y acciones. Las versiones TEST habilitadas o sin
  supresión se avisan.
- Macros y lookups que usan las búsquedas y no están en el paquete (un aviso por macro, con las búsquedas que la usan),
  CSV de los lookups (cabecera y nº de columnas) y XML de los dashboards.

**Validar en el cliente** (tarea cancelable): versión instalada (avisa si el paquete no es más nuevo), macros y lookups
que no existen ni en el paquete ni en el cliente o no se ven desde la app, y el SPL de cada búsqueda pasado por el
**parser de Splunk** (`search/parser` con `parse_only`, que no ejecuta nada, ni siquiera las subbúsquedas). Se guarda en
`checks/` y se puede volver a ver.

**Cambios (pestaña Cambios).** Frente a otra versión subida o a lo que hay instalado en un cliente: objetos nuevos,
modificados y eliminados (saved searches, macros, eventtypes, transforms, props, permisos, lookups, dashboards…) con las
diferencias campo a campo. Entre versiones se distingue `default/` y `local/` (los campos de local llevan `[local]`).
Contra un cliente se comparan los campos que define el paquete con los de la app instalada (en el cliente no se puede
saber qué viene de su `local/`, p. ej. una alerta editada desde Splunk Web), y de los lookups solo si están (Splunk no
deja leer su contenido por REST). Las saved searches que no cambian pero usan una macro que sí cambia aparecen como
«Usa macros que cambian».

**Ejecución (pestaña Ejecución).** Ejecuta en un cliente el SPL de las búsquedas elegidas (por defecto, las nuevas, las
que cambian de SPL y las que usan macros que cambian), con la ventana de cada alerta o un rango fijo:
- **Solo búsquedas ad hoc** (`search/jobs`): nunca se lanza la saved search, así que no se envían emails, notables ni
  webhooks. No se crea ni modifica nada en Splunk y cada job se borra al terminar.
- No se ejecuta nada que escriba o envíe datos: `outputlookup`, `collect`, `sendemail`, `sendalert`, `delete`,
  `script`… y los comandos que empiezan por send/output/notify/post/push/write/ingest. Se comprueba en el texto (con las
  macros del paquete ya sustituidas) y en los comandos que devuelve el parser con las macros del cliente.
- Con «Usar las macros del paquete» se sustituyen por su definición antes de ejecutar, para probar la versión nueva
  aunque el cliente tenga las antiguas (las de tipo eval y los lookups o eventtypes nuevos se resuelven con lo del
  cliente). Las alertas en tiempo real se ejecutan como búsqueda histórica con la misma ventana.
- Opcionalmente se ejecuta también la versión anterior (la de la versión comparada o la instalada en el cliente) para
  comparar el nº de resultados. Una detrás de otra, con tiempo máximo por búsqueda, progreso y cancelación. Resultado de
  cada una: OK, con avisos, error (con el mensaje de Splunk), no ejecutada, tiempo agotado o cancelada, con resultados,
  eventos, duración y la diferencia con la versión anterior. Se guarda en `runs/`.

**Exportar informe**: Excel, HTML y PDF en `informes/` (aparecen también en «Informes») con los problemas, los cambios
respecto a la versión comparada y la validación y ejecución elegidas.

## Historial de backups

Cada backup (desde la web o el CLI) guarda además una copia con fecha en
`backups/<tipo>/historial/<nombre del archivo>/AAAAMMDD-HHMMSS.json`, solo si ha cambiado respecto a la versión
anterior, y se conservan las 30 más recientes (`MAX_VERSIONS` en `core/backup_files.py`). Al comparar versiones se
ignoran los campos que Splunk cambia solo (`next_scheduled_time`, `triggered_alert_count`). El archivo "actual"
sigue teniendo el mismo nombre de siempre, así que los restores del CLI no cambian.

## Credenciales

Cada cliente puede tener **usuario/contraseña, token o ambos**. Con los dos se elige el **método preferido** y, si Splunk
rechaza esas credenciales, se prueba automáticamente el otro (la web avisa de que se conectó con el método alternativo).
Con un token se comprueba al conectar que es válido.

Las contraseñas y tokens se guardan **cifrados** en `config.py` (`"password": "dpapi:…"`) con **DPAPI de Windows**: solo
los puede descifrar el mismo usuario de Windows (en un dominio con perfil móvil, también en otro equipo con ese usuario).
Si se copia `config.py` a otro usuario o equipo, la aplicación avisa de qué credenciales hay que volver a introducir.

Los `config.py` antiguos se migran solos al arrancar la web o el CLI: se cifran las credenciales en
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

## Tareas programadas (CLI sin preguntas)

Con argumentos, `main_cli.py` (o `Splunk_Manager_CLI.exe`) no pregunta nada y se puede programar con el Programador de
tareas de Windows. `python main_cli.py --help` muestra todas las opciones.

```
main_cli.py --backup alertas macros --clientes todos               # backup de alertas CU-* (y TEST - CU-*) y macros de todos
main_cli.py --backup todo --clientes todos --excluir PRUEBAS        # alertas, es, reportes, macros, usuarios y roles
main_cli.py --informe --clientes todos --dias 7                     # informe semanal (Excel + PDF) de todos
main_cli.py --informe --por-cliente --clientes CLI1,CLI2 --secciones salud,analisis --formatos xlsx,pdf
main_cli.py --deshabilitar "CU-AD-001,CU-FW-*" --clientes todos --simular    # qué se deshabilitaría
main_cli.py --listar-clientes
```

- **Clientes**: `--clientes todos` o nombres separados por comas (sin distinguir mayúsculas); `--excluir` quita algunos.
- **Backups** (`--backup`): `alertas` (CU-\* y TEST - CU-\*), `es`, `reportes`, `macros`, `usuarios`, `roles` o `todo`.
  Se guardan igual que desde la web, con su historial de versiones, y leen las saved searches de todas las apps y usuarios.
- **Informes** (`--informe`): secciones `salud`, `ingesta`, `alertas`, `analisis`, `indices`, `usuarios` (por defecto
  todas), formatos `xlsx`, `html`, `pdf` (por defecto Excel y PDF), `--dias 7|30|90` (por defecto 7) y `--por-cliente`
  para un archivo por cliente. Se guardan en `informes/`.
- **Habilitar / deshabilitar** (`--habilitar`, `--deshabilitar`): nombres separados por comas, con comodines
  (`CU-AD-*`, `TEST - CU-*`), en todas las apps y usuarios; `--simular` muestra lo que cambiaría sin tocar nada. Antes de cambiar nada se guarda la configuración en
  el backup «antes_de_editar». También está en el menú interactivo (opción 19), junto al análisis de alertas (opción 20).
- Todo lo que se muestra se guarda en `logs/AAAAMMDD-HHMMSS_<acción>.log` (se conservan los 200 últimos; `--sin-log` lo
  desactiva). **Código de salida**: 0 correcto, 1 algún cliente u operación falló (ver el log), 2 argumentos no válidos.
  El Programador de tareas lo muestra en «Resultado de la última ejecución».

Ejemplo para crear las tareas desde `cmd`, con el ejecutable del CLI en `C:\SplunkManager` (`/RP` pide la contraseña de
Windows del usuario):

```
schtasks /Create /TN "Splunk Manager\Backup nocturno" /SC DAILY /ST 02:00 /RU %USERDOMAIN%\%USERNAME% /RP /TR "\"C:\SplunkManager\Splunk_Manager_CLI.exe\" --backup todo --clientes todos"
schtasks /Create /TN "Splunk Manager\Informe semanal" /SC WEEKLY /D MON /ST 07:00 /RU %USERDOMAIN%\%USERNAME% /RP /TR "\"C:\SplunkManager\Splunk_Manager_CLI.exe\" --informe --clientes todos --dias 7"
```

Con Python en lugar del ejecutable: `/TR "\"C:\ruta\python.exe\" \"C:\ruta\SPLUNK_manager\main_cli.py\" --backup todo --clientes todos"`
(mejor la ruta real de `python.exe` que el alias de la Microsoft Store). Importante:

- La tarea debe ejecutarse **con el mismo usuario de Windows** que guardó las credenciales: están cifradas con DPAPI y
  ningún otro usuario (tampoco SYSTEM) puede descifrarlas.
- Con «Ejecutar tanto si el usuario inició sesión como si no» hay que **guardar la contraseña** de Windows en la tarea.
  Con «No almacenar contraseña» Windows no da acceso a DPAPI y las conexiones fallarán.
- `config.py`, `backups/`, `informes/` y `logs/` son siempre los de la carpeta del script o del `.exe`, sea cual sea la
  carpeta de inicio de la tarea.

## Generar ejecutables

```
python -m PyInstaller Splunk_Manager_CLI.spec   # CLI  -> dist/Splunk_Manager_CLI.exe
python -m PyInstaller Splunk_Manager_Web.spec   # Web  -> dist/Splunk_Manager_Web.exe
```

El `.exe` busca `config.py`, `backups/` e `informes/` en su misma carpeta. `config.py` nunca se incluye dentro del
ejecutable (`excludes=['config']` en los .spec).
