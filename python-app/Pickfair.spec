# -*- mode: python ; coding: utf-8 -*-
import sys
import os
import glob
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# Find SSL DLLs automatically
def find_ssl_dlls():
    """Find OpenSSL DLLs in Python installation or system paths."""
    ssl_dlls = []
    dll_patterns = [
        'libssl*.dll', 
        'libcrypto*.dll',
        'ssleay*.dll',
        'libeay*.dll',
    ]
    
    search_paths = [
        # Python installation paths
        os.path.dirname(sys.executable),
        os.path.join(os.path.dirname(sys.executable), 'DLLs'),
        os.path.join(os.path.dirname(sys.executable), 'Library', 'bin'),
        os.path.join(os.path.dirname(sys.executable), 'Scripts'),
        # OpenSSL common paths
        'C:/OpenSSL-Win64/bin',
        'C:/OpenSSL-Win32/bin', 
        'C:/Program Files/OpenSSL-Win64/bin',
        'C:/Program Files/OpenSSL/bin',
        'C:/Program Files (x86)/OpenSSL/bin',
        # Chocolatey OpenSSL
        'C:/ProgramData/chocolatey/lib/openssl/tools/openssl/bin',
        'C:/tools/openssl/bin',
        # MinGW/MSYS2
        'C:/msys64/mingw64/bin',
        'C:/mingw64/bin',
        # System paths
        'C:/Windows/System32',
        'C:/Windows/SysWOW64',
        # Environment variable
        os.environ.get('OPENSSL_DIR', ''),
        os.path.join(os.environ.get('OPENSSL_DIR', ''), 'bin'),
    ]
    
    # Also search PATH
    path_dirs = os.environ.get('PATH', '').split(os.pathsep)
    search_paths.extend(path_dirs)
    
    found_files = set()
    for path in search_paths:
        if path and os.path.exists(path):
            for pattern in dll_patterns:
                matches = glob.glob(os.path.join(path, pattern))
                for dll_path in matches:
                    dll_name = os.path.basename(dll_path).lower()
                    if dll_name not in found_files:
                        # Only include libssl and libcrypto (OpenSSL 3.x)
                        if 'libssl' in dll_name or 'libcrypto' in dll_name:
                            ssl_dlls.append((dll_path, '.'))
                            found_files.add(dll_name)
                            print(f"[SSL] Found: {dll_path}")
    
    if not ssl_dlls:
        print("[SSL] WARNING: No SSL DLLs found! App may use slow Python encryption.")
        print("[SSL] Searched paths:")
        for p in search_paths[:10]:
            if p:
                print(f"  - {p}")
    else:
        print(f"[SSL] Total DLLs found: {len(ssl_dlls)}")
    
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
