def list_active_cases(service):
    """
    Lista todos los casos de uso activos que empiezan con 'CU-'.
    """
    print("\nCasos de uso activos que comienzan con 'CU-':")
    try:
        my_saved_searches = service.saved_searches.list()
        for search in my_saved_searches:
            if search.name.startswith("CU-"):
                print(f"{search.name}")
    except Exception as e:
        print(f"Error al listar los casos de uso: {e}")