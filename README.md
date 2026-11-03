# Splunk Manager

Herramienta para administrar varias plataformas Splunk: backup y restore de alertas, macros, usuarios, roles,
dashboards y reportes (también en modo batch sobre varios clientes), edición de alertas (también en varios clientes a
la vez), dependencias de cada alerta (macros y lookups), inventario de lookups, análisis de alertas ruidosas y muertas,
comparación de clientes, validación de versiones de la app de SIRT antes de desplegarlas, informes en Excel/HTML/PDF y,
a través del Admin Config Service (ACS) de Splunk Cloud, actualización de add-ons, tokens HEC, listas de IP permitidas
y puertos de salida, tokens de autenticación, estado del stack y ventanas de mantenimiento, reinicio y exportación de
apps. Todos los cambios quedan en un registro de auditoría. El CLI se puede programar con el Programador de tareas de
Windows para hacer backups, inventarios e informes automáticos.

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
  acs.py                 Admin Config Service (ACS) de Splunk Cloud: apps y su actualización, estado y reinicio, ventanas de
                         mantenimiento, tokens HEC, listas de IP y puertos de salida, tokens JWT y exportación de apps
  audit.py               Registro de auditoría (logs/auditoria.jsonl): qué se cambió, dónde, cuándo, desde dónde y resultado
  splunkbase_account.py  Cuenta de Splunkbase guardada en config.py (contraseña cifrada)
options/
  alerts/                Backup / restore / listado / habilitar y deshabilitar alertas (incl. Enterprise Security)
  addons/                Apps y add-ons de Splunkbase con actualización pendiente y su actualización vía ACS
  platform/              Plataforma (ACS) desde la consola: estado, inventarios (HEC, acceso, tokens, índices), export, reinicio
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
  addons.py              Add-ons: pendientes por cliente y de todos, actualización vía ACS (también en varios clientes) y cuenta
  hec.py                 Tokens HEC vía ACS: inventario con avisos, alta/edición/borrado, backup y restore
  access.py              Listas de IP permitidas y puertos de salida vía ACS: inventario, cambios en varios clientes, backup
  platform.py            Estado del stack y reinicio, mantenimiento, tokens de autenticación y exportación de apps (.tgz)
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
  La comprobación «plataforma» añade, por cliente, el tipo (Cloud Victoria/Classic o Enterprise), la **caducidad del
  token** (aviso a 30 días, problema a 7 y crítico si ha caducado), si el stack tiene un **reinicio pendiente**, los
  **add-ons con actualización pendiente**, la **próxima ventana de mantenimiento** de Splunk (si es en menos de 7 días) y
  los **change freezes** vigentes.
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

  En Splunk Cloud con ACS se consultan las **ventanas de mantenimiento** de Splunk del periodo: se avisa de ellas y, en
  la pestaña Planificador, de cuántos saltos de cada alerta cayeron en esos días (lo normal es que se deban al
  mantenimiento y no a un problema de la alerta).

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
  versiones y estado. En Apps, **Exportar apps…** descarga vía ACS la app completa como `.tgz` (`default/`, `local/` y
  `users/`, o solo los `.conf`) a `backups/apps/<stack>/` (se conservan las 10 últimas de cada app), con opción de
  guardarla también en «Validar app» para comparar versiones. Requiere la capacidad `export_apps`; las apps premium (ES,
  ITSI) y las de Splunk no se pueden exportar.
- **Tokens HEC** (ACS): tokens del HTTP Event Collector del cliente con índice por defecto, índices permitidos, source,
  sourcetype y estado, y avisos cuando el índice por defecto no existe o está deshabilitado, hay índices permitidos que
  no existen o el índice por defecto no está entre ellos. Alta, edición (PATCH, solo lo que cambia), habilitar o
  deshabilitar, borrado (guardando antes su configuración), valor del token bajo demanda (queda en la auditoría),
  **backup y restore** (`backups/hec/`, incluido el valor del token para recrearlo igual) y tarjeta «Todos los clientes».
  Cada cambio se puede **simular**: muestra la petición que se enviaría a ACS.
- **Acceso de red** (ACS): listas de IP permitidas por funcionalidad (API de búsqueda, Splunk Web, HEC, forwarders S2S,
  IDM, ACS; IPv4 e IPv6) y puertos de salida con sus destinos. Añadir o quitar subredes en **uno o varios clientes a la
  vez** como tarea (con simulación; antes de quitar se guarda el estado en `backups/acceso/`), backup y matriz de
  «Todos los clientes» que resalta las subredes repetidas en varios clientes (las de SIRT) frente a las de un solo cliente.
- **Plataforma** (ACS): estado del stack (experiencia Victoria/Classic, versión, estado, reinicio pendiente), ventanas de
  mantenimiento de Splunk de los próximos 30 días, change freezes y ventana preferida; **reinicio** del search head o
  rolling restart del cluster como tarea que espera a que el stack vuelva (con simulación); **tokens de autenticación**
  del stack con caducidad y último uso, borrado y **creación de un token nuevo** con usuario y contraseña del stack, que
  se puede guardar directamente como token del cliente (cifrado) para renovarlo antes de que caduque; exportaciones de
  apps guardadas; y tabla de todos los clientes con la caducidad de cada token (sin conectar) y, a petición, el estado
  de cada stack.
- **Ingesta y licencia**: GB/día de los últimos 7, 30 o 90 días según `license_usage.log`, por índice y sourcetype,
  con su tendencia. Muestra también los entitlements de la suscripción de Splunk Cloud (licencia de ingesta, DDAS,
  DDAA y restauración DDAA, con la misma búsqueda que la Cloud Monitoring Console; requiere el rol `sc_admin`), el % de
  uso de la licencia y los días que la superan. Los informes incluyen los mismos datos.
- **Backups**: archivos por tipo, visor del contenido, descarga, importación de JSON, borrado y restore
  (también de solo algunos elementos de un archivo), y **historial de versiones** con comparación y restore de
  una versión anterior.
- **Operaciones masivas**: un mismo backup o restore en varios clientes a la vez.
- **Comparar clientes**: un cliente de referencia frente a uno o varios clientes (alertas, reportes, macros,
  dashboards, roles, usuarios, índices, apps y, vía ACS, **tokens HEC, listas de IP y puertos de salida**): qué falta,
  qué sobra y qué es distinto, con las diferencias campo a campo. Los tokens HEC se copian creándolos con el mismo valor
  o actualizándolos; en las listas y puertos solo se añaden las subredes que faltan (nunca se quita ninguna). Las alertas, reportes, macros, dashboards y roles se pueden **copiar** de un cliente a otros; antes de
  sobrescribir se guarda lo que había en `backups/<tipo>/…antes_de_copiar_<host>_backup.json`. En alertas y reportes,
  **Emparejar TEST y producción** pone `TEST - CU-X` y `CU-X` en la misma fila para ver en qué fase está cada cliente
  (en cada uno se compara la misma fase que en la referencia, o la otra si no la tiene); la copia usa el nombre real.
- **Validar app**: validación de una versión de la app de SIRT antes de desplegarla (ver «Validar app»).
- **Add-ons**: apps y add-ons de Splunkbase con una versión nueva en el cliente activo (las del aviso «Update» de Manage
  Apps), con sus notas (cambio de versión mayor, deshabilitada, no gestionable por ACS) y enlace a Splunkbase; se
  actualizan las seleccionadas o todas como tarea cancelable, de una en una vía ACS. Tarjeta «Todos los clientes» con
  las pendientes de cada cliente (pulsar una fila cambia a ese cliente), desde la que se pueden **actualizar las
  pendientes de varios clientes a la vez** (cliente a cliente, con opción de dejar fuera las que cambian de versión
  mayor), y gestión de la cuenta de Splunkbase (se comprueba en Splunkbase antes de guardarla cifrada). Ver
  «Actualizar add-ons y apps de Splunkbase».
- **Informes**: informe de uno o varios clientes con salud, ingesta, inventario y análisis de alertas, índices,
  usuarios/roles y **plataforma** (tipo y experiencia, versión del stack, reinicio pendiente, caducidad del token,
  tokens del stack, add-ons pendientes, tokens HEC, listas de IP, puertos de salida, próxima ventana y change freezes),
  en **Excel**, **HTML** (para ver o imprimir) y **PDF** (se genera con Microsoft Edge en modo headless). Se guardan en
  `informes/`. También desde «Salud de clientes» → «Exportar informe…».
- **Clientes**: alta, edición, borrado y prueba de cada credencial antes de guardar (las contraseñas y tokens nunca
  se envían al navegador). Cada cliente puede indicar su **App SIRT** si allí no se llama `sirt_soc_app_for_splunk`.
  La columna **Plataforma** indica si es Splunk Cloud (y Victoria o Classic una vez consultado ACS) o Enterprise, si ACS
  está disponible (hace falta el token de un `sc_admin`) y cuándo caduca el token; las secciones de ACS avisan en lugar
  de fallar cuando el cliente no lo admite.
- **Auditoría**: registro de todos los cambios hechos desde la web o el CLI (ediciones de alertas y macros, habilitar o
  deshabilitar, restores, copias entre clientes, add-ons, tokens HEC, listas de IP, tokens, reinicios, exportaciones,
  altas y bajas de clientes), con fecha, origen, usuario de Windows, cliente, resultado y detalle (nunca credenciales),
  filtros y exportación a CSV. Las tareas se registran al iniciarse y al terminar. Se guarda en `logs/auditoria.jsonl`
  (al superar 20 MB se renombra con la fecha). Los backups «antes de editar» siguen guardando el contenido anterior.
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

## Actualizar add-ons y apps de Splunkbase (ACS)

Sección **Gestión → Add-ons** de la web, opción **21** del menú del CLI y `--addons` / `--actualizar-addons` en modo no
interactivo. Las «pendientes» son las apps
para las que el propio Splunk ya ha detectado una versión nueva en Splunkbase (el aviso «Update» de Apps → Manage Apps,
campos `update.*` de `apps/local`). Se actualizan a través del **Admin Config Service (ACS)** de Splunk Cloud
(`PATCH /adminconfig/v2/apps/victoria/<app>`), igual que lo haría el botón de Splunk Web, de una en una y esperando a que
ACS termine cada una.

Requisitos:
- Stack de Splunk Cloud con **Victoria Experience** y el **token** del cliente en `config.py` (`splunkToken`): ACS no
  admite usuario/contraseña y el usuario del token debe tener el rol `sc_admin`.
- Una **cuenta de Splunkbase** (usuario y contraseña de splunk.com), con la que ACS descarga la app. Se puede guardar en
  `config.py` desde la sección Add-ons de la web o la opción 21 del CLI (`splunkbase = {...}`; la contraseña se cifra
  con DPAPI como las demás), indicarla con las variables de entorno `SPLUNKBASE_USERNAME` y `SPLUNKBASE_PASSWORD`, o
  con `--splunkbase-usuario` más `SPLUNKBASE_PASSWORD`.

Antes de actualizar se guarda en `backups/addons/addons_<host>_backup.json` (con historial) la lista de apps con su
versión, por si hay que volver a una versión anterior (ACS permite instalar una versión concreta). Las apps que no constan
en ACS como de Splunkbase (instaladas por soporte de Splunk) se muestran pero no se pueden actualizar desde aquí, y los
saltos de versión mayor (11.x → 12.0) se marcan para revisar antes sus notas de la versión. Si la licencia que espera ACS
no es la que publica Splunkbase, se repite la petición con la que indica ACS.

## Plataforma vía ACS: requisitos y límites

Todo lo de ACS (add-ons, tokens HEC, listas de IP y puertos, estado y reinicio, mantenimiento, tokens y exportación de
apps) usa la API REST del Admin Config Service (`https://admin.splunk.com/<stack>/adminconfig/v2/...`), la misma que el
ACS CLI de Splunk, así que no hace falta instalar el CLI. Requisitos y límites:

- Solo **Splunk Cloud** (`*.splunkcloud.com`): en Splunk Enterprise esas secciones avisan de que no hay ACS.
- El **token** del cliente debe ser de un usuario con el rol `sc_admin` (exportar apps necesita además la capacidad
  `export_apps`). Si el token ha caducado, Plataforma → Tokens permite crear otro con usuario y contraseña del stack
  y guardarlo como token del cliente. Los tokens solo valen en el search head donde se crearon.
- ACS responde `409` si hay otra operación en curso en el stack: las tareas van cliente a cliente y app a app. Las
  altas y cambios de HEC son asíncronos (ACS responde 202) y se espera a que queden aplicados.
- `federated-search` solo tiene lista IPv6 en ACS. Los stacks FedRAMP no admiten tokens ni exportación.
- Todas las operaciones de escritura admiten **simulación** (web: botón o casilla «Simular»; CLI: `--simular`) que
  muestra el método, la URL y el cuerpo de la petición que se enviaría sin enviarla, y quedan en la auditoría.
- Los inventarios (HEC, listas, tokens, índices, exportaciones) se guardan en `backups/<tipo>/` con historial, así que
  un inventario programado deja constancia de cómo estaba cada stack cada semana.

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
main_cli.py --addons --clientes todos                              # apps y add-ons con actualización pendiente
main_cli.py --actualizar-addons "Splunk_TA_windows,TA-*" --clientes CLI1 --simular   # qué se actualizaría
main_cli.py --actualizar-addons todos --clientes CLI1              # actualiza con la cuenta de Splunkbase de config.py
main_cli.py --actualizar-addons todos --sin-mayores --clientes todos   # todas menos las que cambian de versión mayor
main_cli.py --estado --hec --acceso --tokens --indices --clientes todos  # inventario de la plataforma (ACS) con backup
main_cli.py --exportar-apps "sirt_soc_app_for_splunk" --clientes todos   # exporta la app como .tgz vía ACS
main_cli.py --reiniciar --clientes CLI1 --simular                  # muestra la petición de reinicio sin enviarla
main_cli.py --listar-clientes
```

- **Clientes**: `--clientes todos` o nombres separados por comas (sin distinguir mayúsculas); `--excluir` quita algunos.
- **Backups** (`--backup`): `alertas` (CU-\* y TEST - CU-\*), `es`, `reportes`, `macros`, `usuarios`, `roles` o `todo`.
  Se guardan igual que desde la web, con su historial de versiones, y leen las saved searches de todas las apps y usuarios.
- **Informes** (`--informe`): secciones `salud`, `ingesta`, `alertas`, `analisis`, `indices`, `usuarios`, `plataforma`
  (por defecto todas), formatos `xlsx`, `html`, `pdf` (por defecto Excel y PDF), `--dias 7|30|90` (por defecto 7) y `--por-cliente`
  para un archivo por cliente. Se guardan en `informes/`.
- **Habilitar / deshabilitar** (`--habilitar`, `--deshabilitar`): nombres separados por comas, con comodines
  (`CU-AD-*`, `TEST - CU-*`), en todas las apps y usuarios; `--simular` muestra lo que cambiaría sin tocar nada. Antes de cambiar nada se guarda la configuración en
  el backup «antes_de_editar». También está en el menú interactivo (opción 19), junto al análisis de alertas (opción 20).
- **Add-ons** (`--addons`, `--actualizar-addons`): apps y add-ons de Splunkbase pendientes de actualizar y su
  actualización vía ACS (ver «Actualizar add-ons y apps de Splunkbase»); `--simular` muestra qué se actualizaría. La
  cuenta de Splunkbase sale de `config.py`, de `SPLUNKBASE_USERNAME`/`SPLUNKBASE_PASSWORD` o de `--splunkbase-usuario`
  más `SPLUNKBASE_PASSWORD`; `--sin-mayores` deja fuera las que cambian de versión mayor. En el menú interactivo es la
  opción 21.
- **Plataforma (ACS)**: `--estado` (experiencia, versión, reinicio pendiente, ventanas de mantenimiento, change freezes
  y caducidad del token), `--hec`, `--acceso`, `--tokens` e `--indices` (inventarios con backup en `backups/hec/`,
  `backups/acceso/`, `backups/tokens/` y `backups/indices/`), `--exportar-apps` (`todos` o nombres; `--sin-local` y
  `--sin-usuarios` limitan el contenido; a `backups/apps/<stack>/`) y `--reiniciar` (rolling restart en clusters);
  `--simular` vale para exportar y reiniciar. En el menú interactivo es la opción 22, con submenú.
- Todas las acciones que cambian algo quedan en `logs/auditoria.jsonl` (ver «Auditoría» en la interfaz web).
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
