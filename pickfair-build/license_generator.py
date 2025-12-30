"""
Pickfair License Generator
Standalone tool for generating license keys
KEEP THIS FILE PRIVATE - DO NOT DISTRIBUTE
"""

import customtkinter as ctk
from tkinter import messagebox
import hashlib
import pyperclip

SECRET_KEY = "PickfairBetfair2024SecretKey"

COLORS = {
    'bg_dark': '#0b111a',
    'bg_card': '#111827',
    'bg_hover': '#1f2937',
    'primary': '#3b82f6',
    'success': '#10b981',
    'text': '#f3f4f6',
    'text_secondary': '#9ca3af',
    'border': '#374151'
}

def generate_license_key(hardware_id):
    """Generate a license key for a given hardware ID"""
    clean_hwid = hardware_id.replace("-", "").replace(" ", "").upper()
    
    if len(clean_hwid) != 16:
        return None
    
    combined = f"{clean_hwid}{SECRET_KEY}"
    hash_obj = hashlib.sha256(combined.encode())
    key_hash = hash_obj.hexdigest()[:20].upper()
    
    license_key = f"PICK-{key_hash[:4]}-{key_hash[4:8]}-{key_hash[8:12]}-{key_hash[12:16]}-{key_hash[16:20]}"
    return license_key


class LicenseGeneratorApp:
    def __init__(self):
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        
        self.root = ctk.CTk()
        self.root.title("Pickfair License Generator")
        self.root.geometry("500x400")
        self.root.configure(fg_color=COLORS['bg_dark'])
        self.root.resizable(False, False)
        
        self.create_ui()
        
    def create_ui(self):
        main_frame = ctk.CTkFrame(self.root, fg_color=COLORS['bg_card'], corner_radius=10)
        main_frame.pack(fill='both', expand=True, padx=20, pady=20)
        
        title_label = ctk.CTkLabel(
            main_frame, 
            text="Pickfair License Generator",
            font=ctk.CTkFont(size=24, weight="bold"),
            text_color=COLORS['text']
        )
        title_label.pack(pady=(20, 5))
        
        subtitle_label = ctk.CTkLabel(
            main_frame, 
            text="Genera chiavi di licenza per i tuoi clienti",
            font=ctk.CTkFont(size=14),
            text_color=COLORS['text_secondary']
        )
        subtitle_label.pack(pady=(0, 30))
        
        hwid_label = ctk.CTkLabel(
            main_frame, 
            text="Hardware ID del cliente:",
            font=ctk.CTkFont(size=14),
            text_color=COLORS['text']
        )
        hwid_label.pack(anchor='w', padx=30)
        
        self.hwid_entry = ctk.CTkEntry(
            main_frame,
            placeholder_text="Es: A1B2-C3D4-E5F6-G7H8",
            width=400,
            height=40,
            font=ctk.CTkFont(size=14)
        )
        self.hwid_entry.pack(pady=(5, 20), padx=30)
        
        generate_btn = ctk.CTkButton(
            main_frame,
            text="Genera Chiave",
            command=self.generate_key,
            width=200,
            height=45,
            font=ctk.CTkFont(size=16, weight="bold"),
            fg_color=COLORS['primary']
        )
        generate_btn.pack(pady=10)
        
        result_label = ctk.CTkLabel(
            main_frame, 
            text="Chiave di licenza generata:",
            font=ctk.CTkFont(size=14),
            text_color=COLORS['text']
        )
        result_label.pack(anchor='w', padx=30, pady=(20, 0))
        
        self.result_entry = ctk.CTkEntry(
            main_frame,
            placeholder_text="La chiave apparira qui...",
            width=400,
            height=40,
            font=ctk.CTkFont(size=14),
            state='readonly'
        )
        self.result_entry.pack(pady=(5, 10), padx=30)
        
        copy_btn = ctk.CTkButton(
            main_frame,
            text="Copia negli Appunti",
            command=self.copy_to_clipboard,
            width=150,
            height=35,
            font=ctk.CTkFont(size=14),
            fg_color=COLORS['success']
        )
        copy_btn.pack(pady=10)
        
    def generate_key(self):
        hardware_id = self.hwid_entry.get().strip().upper()
        
        if not hardware_id:
            messagebox.showerror("Errore", "Inserisci l'Hardware ID del cliente")
            return
        
        clean_hwid = hardware_id.replace("-", "").replace(" ", "")
        if len(clean_hwid) != 16:
            messagebox.showerror("Errore", "Hardware ID non valido. Deve essere di 16 caratteri (es: A1B2-C3D4-E5F6-G7H8)")
            return
        
        license_key = generate_license_key(hardware_id)
        
        if license_key:
            self.result_entry.configure(state='normal')
            self.result_entry.delete(0, 'end')
            self.result_entry.insert(0, license_key)
            self.result_entry.configure(state='readonly')
        else:
            messagebox.showerror("Errore", "Impossibile generare la chiave")
    
    def copy_to_clipboard(self):
        self.result_entry.configure(state='normal')
        key = self.result_entry.get()
        self.result_entry.configure(state='readonly')
        
        if key and key != "La chiave apparira qui...":
            try:
                pyperclip.copy(key)
                messagebox.showinfo("Copiato", "Chiave copiata negli appunti!")
            except Exception:
                self.root.clipboard_clear()
                self.root.clipboard_append(key)
                messagebox.showinfo("Copiato", "Chiave copiata negli appunti!")
        else:
            messagebox.showwarning("Attenzione", "Genera prima una chiave!")
    
    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = LicenseGeneratorApp()
    app.run()
