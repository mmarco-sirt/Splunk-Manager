from flask import Flask, render_template, jsonify, request
import os
from config import clientes
from utils import select_client, connect_to_splunk, ensure_backup_dir
from options.alerts.backup_alerts import backup_alerts, backup_alerts_especific
from options.alerts.restore_alerts import restore_alerts
from options.alerts.list_alerts import list_active_cases
from options.macros.backup_macros import backup_macros
from options.macros.restore_macros import restore_macros
from options.users.backup_users import backup_users
from options.users.restore_users import restore_users

app = Flask(__name__, template_folder="web", static_folder="web")

# Asegurar que la carpeta backups existe
ensure_backup_dir()

# Conexión inicial
current_client = select_client(clientes)
service = connect_to_splunk(current_client)

@app.route("/")
def index():
    """Carga la interfaz web."""
    return render_template("index.html")

@app.route("/api/list_files", methods=["GET"])
def list_files():
    """Lista archivos de backup."""
    files = os.listdir("backups")
    return jsonify({"files": [f for f in files if f.endswith(".json")]})


@app.route("/api/backup_alerts", methods=["POST"])
def backup_alerts_api():
    """Realiza backup de alertas."""
    data = request.json
    tipo = data.get("tipo")  # "general" o tecnología específica

    try:
        if tipo == "general":
            backup_alerts(service)
        else:
            backup_alerts_especific(service, tipo)
        return jsonify({"status": "success", "message": "Backup realizado con éxito"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/restore_alerts", methods=["POST"])
def restore_alerts_api():
    """Restaura alertas desde un archivo JSON."""
    data = request.json
    file_name = data.get("file_name")

    if not file_name:
        return jsonify({"status": "error", "message": "Debe seleccionar un archivo"}), 400

    try:
        restore_alerts(service)
        return jsonify({"status": "success", "message": f"Restauración de {file_name} completada"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/backup_macros", methods=["POST"])
def backup_macros_api():
    """Realiza backup de macros."""
    try:
        backup_macros(service)
        return jsonify({"status": "success", "message": "Backup de macros realizado con éxito"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/restore_macros", methods=["POST"])
def restore_macros_api():
    """Restaura macros desde un backup."""
    try:
        restore_macros(service)
        return jsonify({"status": "success", "message": "Restauración de macros completada"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/list_alerts", methods=["GET"])
def list_alerts_api():
    """Lista alertas activas."""
    try:
        alerts = list_active_cases(service)
        return jsonify({"status": "success", "alerts": alerts})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/change_client", methods=["POST"])
def change_client_api():
    """Cambia el cliente activo en Splunk."""
    global service, current_client
    try:
        current_client = select_client(clientes)
        service = connect_to_splunk(current_client)
        return jsonify({"status": "success", "message": "Cliente cambiado con éxito"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/clients")
def get_clients():
    """Devuelve la lista de clientes en formato JSON."""
    return jsonify(clientes)

@app.route("/api/connect", methods=["POST"])
def connect():
    """Recibe un cliente y conecta a Splunk."""
    data = request.json  # Recibe los datos en JSON
    client_name = data.get("client")

    # Busca el cliente en la lista
    client = next((c for c in clientes if c["name"] == client_name), None)

    if not client:
        return jsonify({"error": "Cliente no encontrado"}), 404

    # Conectar a Splunk
    service = connect_to_splunk(client)

    if service:
        return jsonify({"message": f"Conectado a {client_name} exitosamente"})
    else:
        return jsonify({"error": "Error al conectar a Splunk"}), 500

if __name__ == "__main__":
    app.run(debug=True)
