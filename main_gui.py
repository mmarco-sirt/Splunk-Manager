import tkinter as tk
from tkinter import messagebox, ttk, simpledialog
import os
import threading
import inspect
import importlib
import sys
import queue

from config import clientes
from utils import connect_to_splunk, ensure_backup_dir
from options.alerts.backup_alerts import (
    backup_alerts, backup_alerts_especific, backup_alerts_custom, 
    backup_es_use_cases, backup_es_use_cases_custom
)
from options.alerts.restore_alerts import restore_alerts, restore_alerts_from_file
from options.alerts.list_alerts import list_active_cases
from options.macros.backup_macros import backup_macros
from options.macros.restore_macros import restore_macros
from options.users.backup_users import backup_users
from options.users.restore_users import restore_users
from options.dashboards.backup_dashboard import backup_dashboard
from options.dashboards.restore_dashboard import restore_dashboard
from options.reports.backup_reports import backup_reports
from options.roles.backup_roles import backup_roles
from options.roles.restore_roles import restore_roles
from batch_mode import batch_backup_alerts, batch_restore_alerts

TECNOLOGIAS = ["AD", "FW", "AV", "PX", "MAIL", "O365", "MI"]

class TextRedirector:
    """Redirige stdout y stderr a un widget de texto de Tkinter de forma segura (usando hilos)."""
    def __init__(self, text_widget):
        self.text_widget = text_widget
        self.queue = queue.Queue()
        self.update_widget()

    def write(self, string):
        self.queue.put(string)

    def flush(self):
        pass

    def update_widget(self):
        while not self.queue.empty():
            text = self.queue.get_nowait()
            self.text_widget.config(state="normal")
            self.text_widget.insert(tk.END, text)
            self.text_widget.see(tk.END)
            self.text_widget.config(state="disabled")
        self.text_widget.after(100, self.update_widget)

class SplunkApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Splunk Manager GUI")
        self.geometry("800x650")
        self.minsize(700, 500)
        self.service = None
        self.current_client = None

        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")

        ensure_backup_dir()
        self.create_widgets()
        
        print("Bienvenido a Splunk Manager GUI.")
        print("Seleccione un cliente en la parte superior y haga clic en 'Conectar' para empezar.\n")

    def create_widgets(self):
        # Header Frame
        header_frame = ttk.Frame(self, padding=10)
        header_frame.pack(fill="x")

        self.client_label = ttk.Label(header_frame, text="🔴 No conectado", font=("Arial", 12, "bold"), width=15)
        self.client_label.pack(side="left", padx=10)

        ttk.Label(header_frame, text="Cliente:").pack(side="left", padx=(10, 5))
        
        self.client_var = tk.StringVar()
        self.client_combo = ttk.Combobox(header_frame, textvariable=self.client_var, state="readonly", width=25)
        self.client_combo['values'] = [c["name"] for c in clientes]
        self.client_combo.pack(side="left", padx=5)
        if clientes:
            self.client_combo.current(0)

        self.btn_connect = ttk.Button(header_frame, text="Conectar", command=self.connect_client)
        self.btn_connect.pack(side="left", padx=5)

        self.btn_creds = ttk.Button(header_frame, text="Editar Credenciales", command=self.edit_credentials)
        self.btn_creds.pack(side="left", padx=5)

        # PanedWindow para redimensionar dinámicamente
        self.paned_window = ttk.PanedWindow(self, orient=tk.VERTICAL)
        self.paned_window.pack(fill="both", expand=True, padx=10, pady=5)

        # Notebook (Tabs)
        self.notebook = ttk.Notebook(self.paned_window)
        self.paned_window.add(self.notebook, weight=2) # 2/3 del espacio

        self.create_alerts_tab()
        self.create_macros_tab()
        self.create_users_roles_tab()
        self.create_dashboards_reports_tab()
        self.create_batch_tab()
        
        self.create_console_widget()

    def add_tab_buttons(self, tab, buttons):
        """Distribuye botones en forma de grid."""
        for i, (text, command) in enumerate(buttons):
            btn = ttk.Button(tab, text=text, command=command)
            btn.grid(row=i // 2, column=i % 2, padx=15, pady=15, sticky="ew", ipady=5)
        
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_columnconfigure(1, weight=1)

    def create_alerts_tab(self):
        tab = ttk.Frame(self.notebook, padding=15)
        self.notebook.add(tab, text="Alerts")
        buttons = [
            ("Backup General Alerts", lambda: self.run_action(backup_alerts)),
            ("Backup Específico (Tecnología)", self.backup_alerts_specific_prompt),
            ("Backup Custom Alerts", self.backup_alerts_custom_prompt),
            ("Backup Enterprise Security", lambda: self.run_action(backup_es_use_cases)),
            ("Restore Alerts", self.restore_alerts_gui),
            ("List Active Alerts", lambda: self.run_action(list_active_cases))
        ]
        self.add_tab_buttons(tab, buttons)

    def create_macros_tab(self):
        tab = ttk.Frame(self.notebook, padding=15)
        self.notebook.add(tab, text="Macros")
        buttons = [
            ("Backup Macros", lambda: self.run_action(backup_macros)),
            ("Restore Macros", self.restore_macros_gui)
        ]
        self.add_tab_buttons(tab, buttons)

    def create_users_roles_tab(self):
        tab = ttk.Frame(self.notebook, padding=15)
        self.notebook.add(tab, text="Users & Roles")
        buttons = [
            ("Backup Users", lambda: self.run_action(backup_users)),
            ("Restore Users", self.restore_users_gui),
            ("Backup Roles", lambda: self.run_action(backup_roles)),
            ("Restore Roles", lambda: self.run_action(restore_roles))
        ]
        self.add_tab_buttons(tab, buttons)

    def create_dashboards_reports_tab(self):
        tab = ttk.Frame(self.notebook, padding=15)
        self.notebook.add(tab, text="Dashboards & Reports")
        buttons = [
            ("Backup Dashboard", self.backup_dashboard_prompt),
            ("Restore Dashboard", self.restore_dashboard_gui),
            ("Backup Reports", lambda: self.run_action(backup_reports))
        ]
        self.add_tab_buttons(tab, buttons)

    def create_batch_tab(self):
        tab = ttk.Frame(self.notebook, padding=15)
        self.notebook.add(tab, text="Batch Operations")
        buttons = [
            ("Batch Backup Alerts (Múltiples Clientes)", self.batch_backup_gui),
            ("Batch Restore Alerts (Múltiples Clientes)", self.batch_restore_gui),
            ("Multi Backup (Varias Alertas, 1 Cliente)", self.multi_alert_backup_gui),
            ("Multi Restore (Varios Archivos, 1 Cliente)", self.multi_alert_restore_gui)
        ]
        self.add_tab_buttons(tab, buttons)

    def create_console_widget(self):
        # Console Frame
        self.console_frame = ttk.LabelFrame(self.paned_window, text="Consola de Salida", padding=5)
        self.paned_window.add(self.console_frame, weight=1) # 1/3 del espacio

        btn_frame = ttk.Frame(self.console_frame)
        btn_frame.pack(side="top", fill="x")
        ttk.Button(btn_frame, text="🧹 Limpiar Consola", command=self.clear_console).pack(side="right", padx=5, pady=2)

        self.console_text = tk.Text(self.console_frame, height=10, state="disabled", bg="#1e1e1e", fg="#00ff00", font=("Consolas", 10))
        self.console_scroll = ttk.Scrollbar(self.console_frame, command=self.console_text.yview)
        self.console_text.configure(yscrollcommand=self.console_scroll.set)
        
        self.console_scroll.pack(side="right", fill="y")
        self.console_text.pack(side="left", fill="both", expand=True)

        # Redirigir stdout y stderr
        sys.stdout = TextRedirector(self.console_text)
        sys.stderr = TextRedirector(self.console_text)

    def connect_client(self):
        selection = self.client_combo.get()
        if not selection:
            print("⚠️ Seleccione un cliente de la lista.")
            return
            
        client_dict = next((c for c in clientes if c["name"] == selection), None)
        if not client_dict:
            return
            
        self.current_client = client_dict
        
        def connect_task():
            self.btn_connect.config(state="disabled", text="Conectando...")
            print(f"\n⏳ Conectando a {client_dict['name']}...")
            
            self.service = connect_to_splunk(self.current_client)
            
            if self.service:
                self.client_label.config(text=f"🟢 {client_dict['name']}", foreground="green")
                print(f"✅ Conexión establecida correctamente.")
            else:
                self.client_label.config(text="🔴 Error de conexión", foreground="red")
                print(f"❌ Error al conectar a {client_dict['name']}.")
                
            self.btn_connect.config(state="normal", text="Conectar")
            
        threading.Thread(target=connect_task, daemon=True).start()

    def clear_console(self):
        self.console_text.config(state="normal")
        self.console_text.delete("1.0", tk.END)
        self.console_text.config(state="disabled")

    def run_action(self, func, *args):
        if not self.service:
            print("⚠️ Sin conexión: Debe conectarse a un cliente primero.")
            return
        
        def task():
            try:
                print(f"\n▶ Ejecutando acción...")
                func(self.service, *args)
                print("✅ Acción completada correctamente.")
            except Exception as e:
                print(f"❌ Error durante la ejecución: {str(e)}")
                
        threading.Thread(target=task, daemon=True).start()

    def backup_alerts_specific_prompt(self):
        win = tk.Toplevel(self)
        win.title("Seleccionar Tecnología")
        win.geometry("300x380")
        
        ttk.Label(win, text="Seleccione tecnología:", font=("Arial", 12)).pack(pady=10)
        
        for tech in TECNOLOGIAS:
            ttk.Button(win, text=tech, command=lambda t=tech: (self.run_action(backup_alerts_especific, t), win.destroy())).pack(pady=3, fill="x", padx=40)

    def backup_alerts_custom_prompt(self):
        name = simpledialog.askstring("Backup Custom Alert", "Nombre de la alerta:")
        if name:
            self.run_action(backup_alerts_custom, name)

    def backup_dashboard_prompt(self):
        dash_name = simpledialog.askstring("Backup Dashboard", "Nombre del Dashboard:")
        if dash_name:
            self.run_action(backup_dashboard, dash_name)
            
    def call_with_file_fallback(self, func, module_name, file_path):
        """Ejecuta una funcion de restauracion. Si la funcion no acepta un parametro de archivo, 
        mockea temporalmente `choose_backup_file` del modulo original para compatibilidad."""
        sig = inspect.signature(func)
        if len(sig.parameters) >= 2:
            func(self.service, file_path)
        else:
            try:
                module = importlib.import_module(module_name)
                original_choose = getattr(module, "choose_backup_file", None)
                if original_choose:
                    setattr(module, "choose_backup_file", lambda: file_path)
                func(self.service)
            finally:
                if original_choose:
                    setattr(module, "choose_backup_file", original_choose)

    def select_backup_file(self, folder, callback, extension=".json"):
        dir_path = os.path.join("backups", folder)
        if not os.path.exists(dir_path):
            print(f"⚠️ Error: No hay carpeta de backups para '{folder}'")
            return

        files = [f for f in os.listdir(dir_path) if f.endswith(extension)]
        if not files:
            print(f"ℹ️ Sin archivos: No hay backups disponibles en '{folder}'")
            return

        win = tk.Toplevel(self)
        win.title(f"Seleccionar backup de {folder}")
        win.geometry("500x400")

        ttk.Label(win, text=f"Seleccione un archivo para restaurar:", font=("Arial", 12)).pack(pady=5)

        search_var = tk.StringVar()
        search_entry = ttk.Entry(win, textvariable=search_var, font=("Arial", 11))
        search_entry.pack(fill="x", padx=15, pady=5)

        frame = ttk.Frame(win)
        frame.pack(fill="both", expand=True, padx=15, pady=5)

        scrollbar = ttk.Scrollbar(frame, orient="vertical")
        listbox = tk.Listbox(frame, yscrollcommand=scrollbar.set, font=("Arial", 10))
        scrollbar.config(command=listbox.yview)

        scrollbar.pack(side="right", fill="y")
        listbox.pack(side="left", fill="both", expand=True)

        def update_list(*args):
            search_term = search_var.get().lower()
            listbox.delete(0, tk.END)
            for f in sorted(files):
                if search_term in f.lower():
                    listbox.insert(tk.END, f)

        search_var.trace_add("write", update_list)
        update_list()

        def on_select():
            selection = listbox.curselection()
            if selection:
                selected_file = listbox.get(selection[0])
                file_path = os.path.join(dir_path, selected_file)
                win.destroy()
                
                def task():
                    try:
                        print(f"\n▶ Restaurando archivo: {os.path.basename(file_path)}...")
                        callback(file_path)
                        print("✅ Restauración completada.")
                    except Exception as e:
                        print(f"❌ Error en la restauración: {str(e)}")
                threading.Thread(target=task, daemon=True).start()

        ttk.Button(win, text="Restaurar Archivo Seleccionado", command=on_select).pack(pady=10, ipadx=10, ipady=5)

    def restore_alerts_gui(self):
        self.select_backup_file("alertas", lambda f: restore_alerts_from_file(self.service, f))

    def restore_macros_gui(self):
        self.select_backup_file("macros", lambda f: self.call_with_file_fallback(restore_macros, "options.macros.restore_macros", f))

    def restore_users_gui(self):
        self.select_backup_file("users", lambda f: self.call_with_file_fallback(restore_users, "options.users.restore_users", f))

    def restore_dashboard_gui(self):
        self.select_backup_file("dashboards", lambda f: self.call_with_file_fallback(restore_dashboard, "options.dashboards.restore_dashboard", f))

    # --- MÉTODOS DE BATCH Y CREDENCIALES ---

    def edit_credentials(self):
        win = tk.Toplevel(self)
        win.title("Editar Credenciales (Sesión Actual)")
        win.geometry("400x320")
        win.grab_set()

        ttk.Label(win, text="Seleccione Cliente:", font=("Arial", 10, "bold")).pack(pady=10)
        
        client_combo = ttk.Combobox(win, state="readonly", width=30)
        client_combo['values'] = [c["name"] for c in clientes]
        client_combo.pack(pady=5)

        frame = ttk.Frame(win, padding=10)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Username:").grid(row=0, column=0, sticky="e", pady=5, padx=5)
        user_entry = ttk.Entry(frame, width=30)
        user_entry.grid(row=0, column=1, pady=5, padx=5)

        ttk.Label(frame, text="Password:").grid(row=1, column=0, sticky="e", pady=5, padx=5)
        pass_entry = ttk.Entry(frame, show="*", width=30)
        pass_entry.grid(row=1, column=1, pady=5, padx=5)

        ttk.Label(frame, text="Token (Opc.):").grid(row=2, column=0, sticky="e", pady=5, padx=5)
        token_entry = ttk.Entry(frame, show="*", width=30)
        token_entry.grid(row=2, column=1, pady=5, padx=5)

        def on_client_selected(e=None):
            sel = client_combo.get()
            client = next((c for c in clientes if c["name"] == sel), None)
            if client:
                user_entry.delete(0, tk.END)
                user_entry.insert(0, client.get("username", ""))
                pass_entry.delete(0, tk.END)
                pass_entry.insert(0, client.get("password", ""))
                token_entry.delete(0, tk.END)
                token_entry.insert(0, client.get("splunkToken", ""))

        client_combo.bind("<<ComboboxSelected>>", on_client_selected)
        if clientes:
            client_combo.current(0)
            on_client_selected()

        def save_creds():
            sel = client_combo.get()
            client = next((c for c in clientes if c["name"] == sel), None)
            if client:
                client["username"] = user_entry.get()
                client["password"] = pass_entry.get()
                client["splunkToken"] = token_entry.get()
                print(f"🔒 Credenciales de '{sel}' actualizadas temporalmente para esta sesión.")
                messagebox.showinfo("Guardado", "Credenciales actualizadas correctamente en memoria.")
                win.destroy()

        ttk.Button(win, text="Guardar Cambios", command=save_creds).pack(pady=10, ipadx=10, ipady=5)

    def select_multiple_clients(self, callback):
        win = tk.Toplevel(self)
        win.title("Seleccionar Clientes para Batch")
        win.geometry("350x450")
        
        ttk.Label(win, text="Seleccione los clientes:", font=("Arial", 12)).pack(pady=10)
        
        frame = ttk.Frame(win)
        frame.pack(fill="both", expand=True, padx=15, pady=5)
        
        scrollbar = ttk.Scrollbar(frame, orient="vertical")
        listbox = tk.Listbox(frame, yscrollcommand=scrollbar.set, font=("Arial", 11), selectmode=tk.MULTIPLE)
        scrollbar.config(command=listbox.yview)

        scrollbar.pack(side="right", fill="y")
        listbox.pack(side="left", fill="both", expand=True)

        for c in clientes:
            listbox.insert(tk.END, c["name"])
            
        def on_accept():
            selected_indices = listbox.curselection()
            selected_clients = [clientes[i] for i in selected_indices]
            if not selected_clients:
                messagebox.showwarning("Selección vacía", "Debe seleccionar al menos un cliente.")
                return
            win.destroy()
            callback(selected_clients)
                
        ttk.Button(win, text="Aceptar", command=on_accept).pack(pady=10, ipadx=10, ipady=5)

    def run_batch_task(self, func, selected_clients):
        def task():
            print("\n▶ Iniciando operación Batch...")
            func(selected_clients)
            print("✅ Operación Batch completada.")
        threading.Thread(target=task, daemon=True).start()

    def batch_backup_gui(self):
        self.select_multiple_clients(lambda cl: self.run_batch_task(batch_backup_alerts, cl))

    def batch_restore_gui(self):
        self.select_multiple_clients(lambda cl: self.run_batch_task(batch_restore_alerts, cl))

    def multi_alert_backup_gui(self):
        if not self.service:
            print("⚠️ Sin conexión: Debe conectarse a un cliente primero.")
            return
        
        alertas_str = simpledialog.askstring("Multi Alert Backup", "Nombres de alertas separados por coma:")
        if alertas_str:
            alertas = [a.strip() for a in alertas_str.split(",") if a.strip()]
            def task():
                for a in alertas:
                    print(f"\n➡ Backup de {a}...")
                    try:
                        backup_alerts_custom(self.service, a)
                    except Exception as e:
                        print(f"❌ Error en backup de {a}: {e}")
                print("✅ Multi Alert Backup completado.")
            threading.Thread(target=task, daemon=True).start()

    def multi_alert_restore_gui(self):
        if not self.service:
            print("⚠️ Sin conexión: Debe conectarse a un cliente primero.")
            return
        
        def callback_multiple(service, file_path):
            restore_alerts_from_file(service, file_path)
            
        # Reutilizamos el selector de archivos pero modificado para aceptar múltiples internamente
        # Para no repetir todo el código de UI, usaremos un truco similar pasándole la lógica a la consola
        self.select_multiple_backup_files("alertas", callback_multiple)

    def select_multiple_backup_files(self, folder, callback, extension=".json"):
        dir_path = os.path.join("backups", folder)
        if not os.path.exists(dir_path):
            print(f"⚠️ Error: No hay carpeta de backups para '{folder}'")
            return

        files = [f for f in os.listdir(dir_path) if f.endswith(extension)]
        if not files:
            print(f"ℹ️ Sin archivos: No hay backups disponibles en '{folder}'")
            return

        win = tk.Toplevel(self)
        win.title(f"Seleccionar múltiples backups de {folder}")
        win.geometry("500x450")

        ttk.Label(win, text="Seleccione uno o más archivos:", font=("Arial", 12)).pack(pady=5)

        search_var = tk.StringVar()
        search_entry = ttk.Entry(win, textvariable=search_var, font=("Arial", 11))
        search_entry.pack(fill="x", padx=15, pady=5)

        frame = ttk.Frame(win)
        frame.pack(fill="both", expand=True, padx=15, pady=5)

        scrollbar = ttk.Scrollbar(frame, orient="vertical")
        listbox = tk.Listbox(frame, yscrollcommand=scrollbar.set, font=("Arial", 10), selectmode=tk.MULTIPLE)
        scrollbar.config(command=listbox.yview)

        scrollbar.pack(side="right", fill="y")
        listbox.pack(side="left", fill="both", expand=True)

        def update_list(*args):
            search_term = search_var.get().lower()
            listbox.delete(0, tk.END)
            for f in sorted(files):
                if search_term in f.lower():
                    listbox.insert(tk.END, f)

        search_var.trace_add("write", update_list)
        update_list()

        def on_select():
            selections = listbox.curselection()
            if not selections:
                messagebox.showwarning("Selección vacía", "Debe seleccionar al menos un archivo.")
                return
            selected_files = [os.path.join(dir_path, listbox.get(i)) for i in selections]
            win.destroy()
            
            def task():
                for file_path in selected_files:
                    try:
                        print(f"\n▶ Restaurando archivo: {os.path.basename(file_path)}...")
                        callback(self.service, file_path)
                        print(f"✅ Restauración de {os.path.basename(file_path)} completada.")
                    except Exception as e:
                        print(f"❌ Error en la restauración de {os.path.basename(file_path)}: {str(e)}")
            threading.Thread(target=task, daemon=True).start()

        ttk.Button(win, text="Restaurar Archivos Seleccionados", command=on_select).pack(pady=10, ipadx=10, ipady=5)

if __name__ == "__main__":
    app = SplunkApp()
    app.mainloop()
