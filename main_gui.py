import tkinter as tk
from tkinter import messagebox
import os

from config import clientes
from utils import select_client, connect_to_splunk, ensure_backup_dir
from options.alerts.backup_alerts import backup_alerts, backup_alerts_especific, backup_alerts_custom, backup_es_use_cases
from options.alerts.restore_alerts import restore_alerts
from options.alerts.list_alerts import list_active_cases
from options.macros.backup_macros import backup_macros
from options.macros.restore_macros import restore_macros
from options.users.backup_users import backup_users
from options.users.restore_users import restore_users
from options.dashboards.backup_dashboard import backup_dashboard
from options.dashboards.restore_dashboard import restore_dashboard


class SplunkApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Splunk Manager GUI")
        self.geometry("500x600")
        self.service = None
        self.current_client = None

        ensure_backup_dir()
        self.create_widgets()
        self.change_client(startup=True)

    def create_widgets(self):
        self.label = tk.Label(self, text="Seleccione una acción:", font=("Arial", 14))
        self.label.pack(pady=10)

        buttons = [
            ("Cambiar Cliente", self.change_client),
            ("Backup Alerts", self.backup_alerts_menu),
            ("Restore Alerts", self.restore_alerts_gui),
            ("Backup Macros", lambda: self.run_action(backup_macros)),
            ("Restore Macros", self.restore_macros_gui),
            ("Backup Users", lambda: self.run_action(backup_users)),
            ("Restore Users", self.restore_users_gui),
            ("Backup Dashboard", self.backup_dashboard_prompt),
            ("Restore Dashboard", self.restore_dashboard_gui),
            ("List Alerts", lambda: self.run_action(list_active_cases)),
            ("Salir", self.quit)
        ]

        for text, command in buttons:
            tk.Button(self, text=text, width=30, command=command).pack(pady=5)

    def change_client(self, startup=False):
        def on_select(client_dict):
            self.current_client = client_dict
            self.service = connect_to_splunk(self.current_client)
            if self.service:
                messagebox.showinfo("Cliente seleccionado", f"Conectado a {client_dict['name']}")
                select_window.destroy()
            else:
                messagebox.showerror("Error", "No se pudo conectar a Splunk")
                if startup:
                    self.destroy()

        select_window = tk.Toplevel(self)
        select_window.title("Seleccionar Cliente")
        select_window.geometry("300x500")
        select_window.grab_set()

        label = tk.Label(select_window, text="Seleccione un cliente:", font=("Arial", 12))
        label.pack(pady=10)

        for client in clientes:
            btn = tk.Button(select_window, text=client["name"], width=25, command=lambda c=client: on_select(c))
            btn.pack(pady=5)

        def on_close():
            if startup:
                messagebox.showwarning("Cliente requerido", "Debes seleccionar un cliente para comenzar.")
                self.destroy()
            else:
                select_window.destroy()

        select_window.protocol("WM_DELETE_WINDOW", on_close)

    def run_action(self, func):
        if not self.service:
            messagebox.showwarning("Sin conexión", "Debe seleccionar un cliente primero")
            return
        try:
            func(self.service)
            messagebox.showinfo("Éxito", "Acción completada correctamente")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def backup_alerts_menu(self):
        if not self.service:
            messagebox.showwarning("Sin conexión", "Debe seleccionar un cliente primero")
            return

        def select_general():
            backup_alerts(self.service)
            messagebox.showinfo("Backup", "Backup general completado")
            win.destroy()

        def select_es():
            backup_es_use_cases(self.service)
            messagebox.showinfo("Backup", "Enterprise Security backup completado")
            win.destroy()

        def select_custom():
            win.withdraw()
            name_win = tk.Toplevel(self)
            name_win.title("Backup Custom Alert")
            name_win.geometry("300x150")
            tk.Label(name_win, text="Nombre de la alerta:").pack(pady=10)
            entry = tk.Entry(name_win)
            entry.pack(pady=5)
            def submit():
                name = entry.get()
                if name:
                    backup_alerts_custom(self.service, name)
                    messagebox.showinfo("Backup", f"Backup de alerta '{name}' completado")
                    name_win.destroy()
                    win.destroy()
            tk.Button(name_win, text="Aceptar", command=submit).pack(pady=10)

        def select_especifico():
            win.withdraw()
            tech_win = tk.Toplevel(self)
            tech_win.title("Seleccionar Tecnología")
            tech_win.geometry("300x300")
            tk.Label(tech_win, text="Seleccione tecnología:", font=("Arial", 12)).pack(pady=10)
            tecnologias = ["AD", "FW", "AV", "PX", "MAIL", "O365", "Monitoring"]
            for tech in tecnologias:
                btn = tk.Button(tech_win, text=tech, width=25,
                                command=lambda t=tech: self._backup_tech(t, tech_win, win))
                btn.pack(pady=3)

        win = tk.Toplevel(self)
        win.title("Backup Alerts")
        win.geometry("300x250")
        tk.Label(win, text="Seleccione tipo de backup:", font=("Arial", 12)).pack(pady=10)
        tk.Button(win, text="1) General", width=25, command=select_general).pack(pady=5)
        tk.Button(win, text="2) Específico por tecnología", width=25, command=select_especifico).pack(pady=5)
        tk.Button(win, text="3) Custom", width=25, command=select_custom).pack(pady=5)
        tk.Button(win, text="4) Enterprise Security", width=25, command=select_es).pack(pady=5)

    def _backup_tech(self, tecnologia, tech_win, parent_win):
        backup_alerts_especific(self.service, tecnologia)
        messagebox.showinfo("Backup", f"Backup de tecnología '{tecnologia}' completado")
        tech_win.destroy()
        parent_win.destroy()

    def backup_dashboard_prompt(self):
        if not self.service:
            messagebox.showwarning("Sin conexión", "Debe seleccionar un cliente primero")
            return

        win = tk.Toplevel(self)
        win.title("Backup Dashboard")
        win.geometry("300x150")

        tk.Label(win, text="Nombre del Dashboard:").pack(pady=10)
        entry = tk.Entry(win)
        entry.pack(pady=5)

        def submit():
            dash_name = entry.get()
            if dash_name:
                backup_dashboard(self.service, dash_name)
                messagebox.showinfo("Dashboard", f"Backup de '{dash_name}' completado")
                win.destroy()

        tk.Button(win, text="Aceptar", command=submit).pack(pady=10)

    def select_backup_file(self, folder, callback, extension=".json"):
        import tkinter.ttk as ttk

        dir_path = os.path.join("backups", folder)
        if not os.path.exists(dir_path):
            messagebox.showerror("Error", f"No hay carpeta de backups para '{folder}'")
            return

        files = [f for f in os.listdir(dir_path) if f.endswith(extension)]
        if not files:
            messagebox.showinfo("Sin archivos", f"No hay archivos en '{folder}'")
            return

        win = tk.Toplevel()
        win.title(f"Seleccionar backup de {folder}")
        win.geometry("500x400")

        tk.Label(win, text=f"Seleccione un archivo para restaurar ({folder}):", font=("Arial", 12)).pack(pady=5)

        # Contenedor con scroll
        container = tk.Frame(win)
        container.pack(fill="both", expand=True, padx=10, pady=5)

        canvas = tk.Canvas(container)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        scrollable_frame = tk.Frame(canvas)

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Barra de búsqueda
        search_var = tk.StringVar()

        def update_search_results():
            search_term = search_var.get().lower()
            for btn in scrollable_frame.winfo_children():
                btn.destroy()  # Limpiamos los botones anteriores

            # Filtrar archivos por nombre (case insensitive)
            filtered_files = [f for f in files if search_term in f.lower()]

            # Crear botones para los archivos filtrados
            for f in sorted(filtered_files):
                btn = tk.Button(scrollable_frame, text=f, anchor="w", width=60,
                                command=lambda file=f: (callback(os.path.join(dir_path, file)), win.destroy()))
                btn.pack(pady=2, padx=5, anchor="w")

        # Crear widget de búsqueda
        search_entry = tk.Entry(win, textvariable=search_var, font=("Arial", 12), width=40)
        search_entry.pack(pady=5)
        search_entry.bind("<KeyRelease>", lambda e: update_search_results())  # Actualizar en tiempo real

        # Llamar a la primera actualización para cargar todos los archivos
        update_search_results()


    def restore_alerts_gui(self):
        self.select_backup_file("alertas", lambda f: restore_alerts(self.service, f))

    def restore_macros_gui(self):
        self.select_backup_file("macros", lambda f: restore_macros(self.service, f))

    def restore_users_gui(self):
        self.select_backup_file("users", lambda f: restore_users(self.service, f))

    def restore_dashboard_gui(self):
        self.select_backup_file("dashboards", lambda f: restore_dashboard(self.service, f))


if __name__ == "__main__":
    app = SplunkApp()
    app.mainloop()
