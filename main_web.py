import argparse
import os
import sys
import threading
import webbrowser

from web.app import LOOPBACK_HOSTS, create_app

PASSWORD_ENV = "SPLUNK_MANAGER_PASSWORD"


def main():
    parser = argparse.ArgumentParser(description="Splunk Manager - interfaz web")
    parser.add_argument("--host", default="127.0.0.1",
                        help="Dirección en la que escuchar (por defecto solo este equipo: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="Puerto (por defecto 8765)")
    parser.add_argument("--no-browser", action="store_true", help="No abrir el navegador automáticamente")
    args = parser.parse_args()

    password = os.environ.get(PASSWORD_ENV)
    local_only = args.host in LOOPBACK_HOSTS
    if not local_only and not password:
        # config.py contiene credenciales de todos los clientes: no se expone en red sin contraseña
        print(f"Para escuchar en '{args.host}' hay que definir una contraseña de acceso en la variable "
              f"de entorno {PASSWORD_ENV}.")
        sys.exit(1)

    app = create_app(password=password, restrict_host=local_only)
    url = f"http://{'127.0.0.1' if local_only else args.host}:{args.port}/"
    print(f"Splunk Manager web disponible en {url}  (Ctrl+C para salir)")
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    app.run(host=args.host, port=args.port, threaded=True, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
