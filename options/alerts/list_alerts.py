from core.conventions import is_cu_alert
from core.knowledge import all_saved_searches


def list_active_cases(service):
    """
    Lista los casos de uso que empiezan por 'CU-' y su versión de pruebas 'TEST - CU-' (de todas las apps y usuarios).
    """
    print("\nCasos de uso que comienzan con 'CU-' o 'TEST - CU-':")
    try:
        for search in all_saved_searches(service):
            if not is_cu_alert(search.name):
                continue
            access = search.access
            if access.get("sharing") == "user":
                print(f"{search.name}  (privada de {access.get('owner')}, app {access.get('app')})")
            elif access.get("sharing") == "app":
                print(f"{search.name}  (solo en la app {access.get('app')})")
            else:
                print(f"{search.name}")
    except Exception as e:
        print(f"Error al listar los casos de uso: {e}")
