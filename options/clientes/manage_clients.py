import os
import sys
import ast
import pprint

# Añadir el directorio raíz del proyecto al path para poder encontrar 'config.py'
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)
CONFIG_PATH = os.path.join(project_root, 'config.py')

def get_clients_from_config():
    """Extrae la lista de 'clientes' desde config.py usando AST para más seguridad."""
    if not os.path.exists(CONFIG_PATH):
        return []
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        source_code = f.read()
    
    tree = ast.parse(source_code)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and \
           isinstance(node.targets[0], ast.Name) and node.targets[0].id == 'clientes':
            try:
                return ast.literal_eval(node.value)
            except ValueError:
                print("❌ Error: No se pudo evaluar la lista 'clientes' en config.py.")
                return None
    return [] # Devuelve lista vacía si la variable no se encuentra

def save_clients_to_config(clients_list):
    """Guarda la lista de clientes actualizada en config.py, preservando el resto."""
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        source_code = f.read()
    
    tree = ast.parse(source_code)
    lines = source_code.splitlines()
    
    clientes_node = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and \
           isinstance(node.targets[0], ast.Name) and node.targets[0].id == 'clientes':
            clientes_node = node
            break
            
    # Formatea la nueva lista para que se vea bien en el archivo
    new_clientes_str = "clientes = " + pprint.pformat(clients_list, indent=4, width=120)

    if not clientes_node:
        # Si la variable 'clientes' no existe, la añade al final.
        with open(CONFIG_PATH, 'a', encoding='utf-8') as f:
            f.write("\n\n" + new_clientes_str + "\n")
    else:
        # Reemplaza la definición existente de 'clientes'
        start_line = clientes_node.lineno - 1
        end_line = getattr(clientes_node, 'end_lineno', start_line) - 1
        
        pre_lines = lines[:start_line]
        post_lines = lines[end_line + 1:]
        
        final_content = "\n".join(pre_lines) + "\n" + new_clientes_str + "\n" + "\n".join(post_lines)
        
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            f.write(final_content)
        
    print("\n✅ `config.py` ha sido actualizado correctamente.")

def modify_client():
    """Modifica un cliente existente en la configuración."""
    clients = get_clients_from_config()
    if not clients:
        print("No se encontraron clientes en `config.py`.")
        return

    print("\n--- Modificar Cliente Existente ---")
    for i, client in enumerate(clients):
        print(f"{i + 1}) {client['name']}")

    try:
        choice = int(input("\nSeleccione el cliente a modificar (número): ")) - 1
        if not (0 <= choice < len(clients)):
            print("Selección inválida.")
            return
    except ValueError:
        print("Entrada no válida.")
        return

    client_to_modify = clients[choice]
    print(f"\nEditando '{client_to_modify['name']}'. Deje en blanco para no cambiar.")
    print("Para credenciales, puede escribir 'BORRAR' para eliminar el campo.")

    # Campos de texto principales
    client_to_modify['name'] = input(f"  - Nombre [{client_to_modify.get('name', '')}]: ") or client_to_modify.get('name')
    client_to_modify['host'] = input(f"  - Host [{client_to_modify.get('host', '')}]: ") or client_to_modify.get('host')

    # Campos de credenciales con opción de borrado
    new_user = input(f"  - Username [{client_to_modify.get('username', '')}]: ")
    if new_user.upper() == 'BORRAR':
        client_to_modify.pop('username', None)
    elif new_user:
        client_to_modify['username'] = new_user

    new_pass = input(f"  - Password [******]: ")
    if new_pass.upper() == 'BORRAR':
        client_to_modify.pop('password', None)
    elif new_pass:
        client_to_modify['password'] = new_pass

    new_token = input(f"  - Token [******]: ")
    if new_token.upper() == 'BORRAR':
        client_to_modify.pop('splunkToken', None)
    elif new_token:
        client_to_modify['splunkToken'] = new_token

    save_clients_to_config(clients)

def add_client():
    """Añade un nuevo cliente a la configuración."""
    clients = get_clients_from_config()

    print("\n--- Añadir Nuevo Cliente ---")
    new_client = {}
    new_client['name'] = input("  - Nombre del cliente: ")
    new_client['host'] = input("  - Host (ej: cliente.splunkcloud.com): ")
    
    print("\n  Introduzca credenciales (deje en blanco si no aplica):")
    new_client['username'] = input("  - Username: ")
    new_client['password'] = input("  - Password: ")
    new_client['splunkToken'] = input("  - Token: ")

    # Limpiar valores vacíos para no guardarlos en el archivo
    new_client = {k: v for k, v in new_client.items() if v}

    if not new_client.get('name') or not new_client.get('host'):
        print("\n❌ Error: El nombre y el host son obligatorios. Cliente no añadido.")
        return

    clients.append(new_client)
    save_clients_to_config(clients)

def main():
    """Menú principal para el script de gestión de clientes."""
    if not os.path.exists(CONFIG_PATH):
        print(f"Aviso: No se encuentra el archivo `config.py`. Se creará uno si añade un cliente.")
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            f.write("clientes = []\n")

    while True:
        print("\n--- Gestión de Clientes de Splunk ---")
        print("1) Modificar un cliente existente")
        print("2) Añadir un nuevo cliente")
        print("0) Salir")
        choice = input("\nSeleccione una opción: ")

        if choice == '1':
            modify_client()
        elif choice == '2':
            add_client()
        elif choice == '0':
            break
        else:
            print("Opción no válida. Intente de nuevo.")

if __name__ == "__main__":
    main()