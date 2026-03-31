import splunklib.client
import os

def select_client(clients):
    """Permite seleccionar un cliente de la lista."""
    print("Seleccione un cliente para conectarse a Splunk:")
    for i, client in enumerate(clients, start=1):
        print(f"{i}. {client['name']}")

    while True:
        try:
            choice = int(input("Ingrese el número del cliente: ")) - 1
            if 0 <= choice < len(clients):
                return clients[choice]
            print("Número fuera de rango, intente nuevamente.")
        except ValueError:
            print("Entrada no válida, ingrese un número.")

def connect_to_splunk(client):
    """Establece la conexión a Splunk usando usuario/contraseña o token."""
    try:
        # Prioritize username/password authentication
        if client.get("username") and client.get("password"):
            service = splunklib.client.connect(
                host=client["host"],
                username=client["username"],
                password=client["password"],
                autologin=True
            )
            print(f"Conectado a Splunk como: {client['username']}")
            return service
        # Fallback to token authentication
        elif client.get("splunkToken"):
            service = splunklib.client.connect(
                host=client["host"],
                splunkToken=client["splunkToken"],
                autologin=True
            )
            print(f"Conectado a {client['name']} usando token.")
            return service
        else:
            print("Error: No se proporcionaron credenciales válidas (usuario/contraseña o token).")
            return None
    except Exception as e:
        print(f"Error al conectar a Splunk: {e}")
        return None

BACKUP_DIR = "backups"

def ensure_backup_dir():
    """Verifica que la carpeta 'backups/' exista y la crea si es necesario."""
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR)