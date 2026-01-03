# -*- mode: python ; coding: utf-8 -*-
import sys
import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# Find SSL DLLs automatically
def find_ssl_dlls():
    """Find OpenSSL DLLs in Python installation or system paths."""
    ssl_dlls = []
    dll_names = ['libssl-3.dll', 'libcrypto-3.dll', 'libssl-3-x64.dll', 'libcrypto-3-x64.dll']
    
    search_paths = [
        os.path.dirname(sys.executable),  # Python root
        os.path.join(os.path.dirname(sys.executable), 'DLLs'),  # Python DLLs folder
        os.path.join(os.path.dirname(sys.executable), 'Library', 'bin'),  # Conda/Anaconda
        'C:/OpenSSL-Win64/bin',
        'C:/OpenSSL-Win32/bin',
        'C:/Program Files/OpenSSL-Win64/bin',
        'C:/Program Files/OpenSSL/bin',
        os.environ.get('OPENSSL_DIR', ''),
    ]
    
    found_dlls = set()
    for path in search_paths:
        if path and os.path.exists(path):
            for dll_name in dll_names:
                dll_path = os.path.join(path, dll_name)
                if os.path.exists(dll_path):
                    base_name = dll_name.replace('-x64', '').replace('-3', '-3')
                    if base_name not in found_dlls:
                        ssl_dlls.append((dll_path, '.'))
                        found_dlls.add(base_name)
                        print(f"[SSL] Found: {dll_path}")
    
    if not ssl_dlls:
        print("[SSL] WARNING: No SSL DLLs found! App may use slow Python encryption.")
        print("[SSL] Install OpenSSL or ensure Python has SSL support.")
    
    return ssl_dlls

# Collect SSL DLLs
ssl_binaries = find_ssl_dlls()

# Hidden imports for all required modules
hidden_imports = [
    'betfairlightweight',
    'betfairlightweight.streaming',
    'betfairlightweight.endpoints',
    'telethon',
    'telethon.crypto',
    'telethon.tl',
    'cryptography',
    'cryptography.hazmat.primitives.ciphers',
    'cryptography.hazmat.backends',
    'cffi',
    '_cffi_backend',
    'numpy',
    'matplotlib',
    'matplotlib.backends.backend_tkagg',
    'PIL',
    'PIL._tkinter_finder',
    'requests',
    'urllib3',
    'certifi',
    'ssl',
    '_ssl',
    'sqlite3',
    'json',
    'threading',
    'queue',
    'asyncio',
    'customtkinter',
    'tkinter',
    'tkinter.ttk',
]

# Collect telethon submodules
hidden_imports += collect_submodules('telethon')
hidden_imports += collect_submodules('betfairlightweight')

# Data files
datas = []
datas += collect_data_files('certifi')
datas += collect_data_files('customtkinter')

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=ssl_binaries,
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Pickfair',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=['libssl*', 'libcrypto*'],  # Don't compress SSL DLLs
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='pickfair.ico' if os.path.exists('pickfair.ico') else None,
)
