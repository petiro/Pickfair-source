# -*- mode: python ; coding: utf-8 -*-
import sys
import os
import glob
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# Find SSL DLLs - PRIORITY: OpenSSL 1.1.1 (Windows 7 compatible)
def find_ssl_dlls():
    """Find OpenSSL 1.1.1 DLLs (required for Windows 7 compatibility).
    
    OpenSSL 3.x is NOT compatible with Windows 7 - it requires Windows 8.1+
    We MUST use OpenSSL 1.1.1 DLLs for Windows 7 support.
    """
    ssl_dlls = []
    
    # STEP 1: Check for bundled DLLs in third_party folder (PREFERRED)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    third_party_paths = [
        os.path.join(script_dir, 'third_party'),
        os.path.join(script_dir, 'third_party', 'openssl'),
        os.path.join(script_dir, 'ssl'),
    ]
    
    for tp_path in third_party_paths:
        if os.path.exists(tp_path):
            # Look for 1.1.1 DLLs first
            for dll_name in ['libssl-1_1-x64.dll', 'libcrypto-1_1-x64.dll', 
                            'libssl-1_1.dll', 'libcrypto-1_1.dll']:
                dll_path = os.path.join(tp_path, dll_name)
                if os.path.exists(dll_path):
                    ssl_dlls.append((dll_path, '.'))
                    print(f"[SSL] BUNDLED (1.1.1): {dll_path}")
    
    # If we found bundled 1.1.1 DLLs, use those exclusively
    if len(ssl_dlls) >= 2:
        print(f"[SSL] Using bundled OpenSSL 1.1.1 DLLs (Windows 7 compatible)")
        return ssl_dlls
    
    # STEP 2: Search for OpenSSL 1.1.1 in system (PREFERRED over 3.x)
    dll_patterns_111 = [
        'libssl-1_1*.dll',    # OpenSSL 1.1.1: libssl-1_1.dll, libssl-1_1-x64.dll  
        'libcrypto-1_1*.dll', # OpenSSL 1.1.1: libcrypto-1_1.dll, libcrypto-1_1-x64.dll
    ]
    
    search_paths = [
        # OpenSSL 1.1.1 installation paths (root and bin)
        'C:/OpenSSL-Win64',
        'C:/OpenSSL-Win64/bin',
        'C:/OpenSSL-Win32',
        'C:/OpenSSL-Win32/bin', 
        'C:/Program Files/OpenSSL-Win64',
        'C:/Program Files/OpenSSL-Win64/bin',
        'C:/Programmi/OpenSSL-Win64',       # Italian Windows
        'C:/Programmi/OpenSSL-Win64/bin',
        'C:/Program Files/OpenSSL',
        'C:/Program Files/OpenSSL/bin',
        'C:/Program Files (x86)/OpenSSL',
        'C:/Program Files (x86)/OpenSSL/bin',
        # Python installation paths
        os.path.dirname(sys.executable),
        os.path.join(os.path.dirname(sys.executable), 'DLLs'),
        os.path.join(os.path.dirname(sys.executable), 'Library', 'bin'),
        # Chocolatey OpenSSL
        'C:/ProgramData/chocolatey/lib/openssl/tools/openssl/bin',
        'C:/tools/openssl/bin',
        # MinGW/MSYS2
        'C:/msys64/mingw64/bin',
        'C:/mingw64/bin',
        # Environment variable
        os.environ.get('OPENSSL_DIR', ''),
        os.path.join(os.environ.get('OPENSSL_DIR', ''), 'bin'),
    ]
    
    # Also search PATH
    path_dirs = os.environ.get('PATH', '').split(os.pathsep)
    search_paths.extend(path_dirs)
    
    # Search for 1.1.1 DLLs first
    found_files = set()
    for path in search_paths:
        if path and os.path.exists(path):
            for pattern in dll_patterns_111:
                matches = glob.glob(os.path.join(path, pattern))
                for dll_path in matches:
                    dll_name = os.path.basename(dll_path).lower()
                    if dll_name not in found_files:
                        ssl_dlls.append((dll_path, '.'))
                        found_files.add(dll_name)
                        print(f"[SSL] Found 1.1.1: {dll_path}")
    
    # If we found 1.1.1 DLLs, use those
    if len(ssl_dlls) >= 2:
        print(f"[SSL] Using OpenSSL 1.1.1 (Windows 7 compatible)")
        return ssl_dlls
    
    # STEP 3: Fallback to 3.x (WARNING: NOT Windows 7 compatible!)
    print("[SSL] WARNING: OpenSSL 1.1.1 not found!")
    print("[SSL] Searching for OpenSSL 3.x (NOT compatible with Windows 7)")
    
    dll_patterns_3x = [
        'libssl-3*.dll',
        'libcrypto-3*.dll',
    ]
    
    # Add System32 only for 3.x fallback
    search_paths_3x = search_paths + ['C:/Windows/System32', 'C:/Windows/SysWOW64']
    
    for path in search_paths_3x:
        if path and os.path.exists(path):
            for pattern in dll_patterns_3x:
                matches = glob.glob(os.path.join(path, pattern))
                for dll_path in matches:
                    dll_name = os.path.basename(dll_path).lower()
                    if dll_name not in found_files:
                        ssl_dlls.append((dll_path, '.'))
                        found_files.add(dll_name)
                        print(f"[SSL] Found 3.x (Win8.1+ only): {dll_path}")
    
    if not ssl_dlls:
        print("[SSL] ERROR: No SSL DLLs found!")
        print("[SSL] Please install OpenSSL 1.1.1w from:")
        print("[SSL] https://slproweb.com/download/Win64OpenSSL_Light-1_1_1w.exe")
    else:
        print(f"[SSL] Total DLLs: {len(ssl_dlls)}")
        if any('3' in os.path.basename(d[0]) for d in ssl_dlls):
            print("[SSL] WARNING: Using OpenSSL 3.x - Windows 7 NOT supported!")
    
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
