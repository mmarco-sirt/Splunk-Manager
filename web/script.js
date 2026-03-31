document.addEventListener("DOMContentLoaded", function () {
    loadClients();
});

function loadClients() {
    fetch("/api/clients")
        .then(response => response.json())
        .then(clients => {
            const clientSelect = document.getElementById("clientSelect");
            clientSelect.innerHTML = "";
            clients.forEach(client => {
                let option = document.createElement("option");
                option.value = client.name;
                option.textContent = client.name;
                clientSelect.appendChild(option);
            });
        })
        .catch(error => console.error("Error cargando clientes:", error));
}

function connectToSplunk() {
    const clientName = document.getElementById("clientSelect").value;
    fetch("/api/connect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ client: clientName }) 
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            document.getElementById("actions").style.display = "block";
            showStatus(`Conectado a Splunk como ${clientName}`, "success");
        } else {
            showStatus("Error al conectar a Splunk", "error");
        }
    })
    .catch(error => console.error("Error en la conexión:", error));
}

function backupAlerts() {
    fetch("/api/backup_alerts", { method: "POST" })
        .then(response => response.json())
        .then(data => showStatus(data.message, data.success ? "success" : "error"))
        .catch(error => console.error("Error en el backup:", error));
}

function showSpecificBackup() {
    document.getElementById("specificBackup").style.display = "block";
}

function backupSpecificAlerts() {
    const tech = document.getElementById("techSelect").value;
    fetch("/api/backup_alerts_specific", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ technology: tech })
    })
    .then(response => response.json())
    .then(data => showStatus(data.message, data.success ? "success" : "error"))
    .catch(error => console.error("Error en el backup específico:", error));
}

function restoreAlerts() {
    fetch("/api/restore_alerts", { method: "POST" })
        .then(response => response.json())
        .then(data => showStatus(data.message, data.success ? "success" : "error"))
        .catch(error => console.error("Error en la restauración:", error));
}

function backupMacros() {
    fetch("/api/backup_macros", { method: "POST" })
        .then(response => response.json())
        .then(data => showStatus(data.message, data.success ? "success" : "error"))
        .catch(error => console.error("Error en el backup de macros:", error));
}

function restoreMacros() {
    fetch("/api/restore_macros", { method: "POST" })
        .then(response => response.json())
        .then(data => showStatus(data.message, data.success ? "success" : "error"))
        .catch(error => console.error("Error en la restauración de macros:", error));
}

function listAlerts() {
    fetch("/api/list_alerts")
        .then(response => response.json())
        .then(data => {
            let message = data.success ? data.alerts.join("<br>") : "Error al listar alertas"; 
            showStatus(message, data.success ? "success" : "error");
        })
        .catch(error => console.error("Error en la lista de alertas:", error));
}

function showStatus(message, type) {
    const statusDiv = document.getElementById("status");
    statusDiv.innerHTML = message;
    statusDiv.style.color = type === "success" ? "green" : "red";
}

document.querySelector("button").addEventListener("click", function() {
    const client = document.getElementById("clientSelect").value;

    fetch("/api/connect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ client: client })
    })
    .then(response => response.json())
    .then(data => {
        if (data.error) {
            alert("Error: " + data.error);
        } else {
            alert("Conectado a " + client);
            document.getElementById("actions").style.display = "block";
        }
    })
    .catch(error => console.error("Error:", error));
});

