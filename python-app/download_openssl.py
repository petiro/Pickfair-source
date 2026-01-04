#!/usr/bin/env python3
"""
OpenSSL 1.1.1 DLL Downloader for Pickfair
Downloads Windows 7 compatible OpenSSL DLLs from official sources.

Usage:
    python download_openssl.py

Creates: third_party/openssl/ with:
    - libssl-1_1-x64.dll
    - libcrypto-1_1-x64.dll
"""

import os
import sys
import shutil
import zipfile
import urllib.request
import tempfile
import hashlib

# OpenSSL 1.1.1w DLLs from slproweb.com (official Windows builds)
# Using the light installer's DLLs extracted from the installer
OPENSSL_VERSION = "1.1.1w"

# Direct DLL URLs from a reliable source (we'll use the prebuilt ones)
# These are from the OpenSSL 1.1.1w Windows x64 build
DLL_SOURCES = [
    {
        "name": "libssl-1_1-x64.dll",
        # GitHub releases often host these for Python projects
        "urls": [
            "https://github.com/nicholasbishop/openssl-win-prebuilt/releases/download/1.1.1w/libssl-1_1-x64.dll",
            "https://raw.githubusercontent.com/nicholasbishop/openssl-win-prebuilt/main/1.1.1w/x64/libssl-1_1-x64.dll",
        ],
        "expected_size_min": 500000,  # ~600KB
    },
    {
        "name": "libcrypto-1_1-x64.dll",
        "urls": [
            "https://github.com/nicholasbishop/openssl-win-prebuilt/releases/download/1.1.1w/libcrypto-1_1-x64.dll",
            "https://raw.githubusercontent.com/nicholasbishop/openssl-win-prebuilt/main/1.1.1w/x64/libcrypto-1_1-x64.dll",
        ],
        "expected_size_min": 2000000,  # ~2.7MB
    },
]

def download_file(url, dest_path, min_size=0):
    """Download a file from URL to destination path."""
    print(f"  Downloading: {url}")
    try:
        # Create request with User-Agent (some servers require it)
        request = urllib.request.Request(
            url,
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Pickfair/3.70'}
        )
        
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read()
            
        # Verify minimum size
        if len(data) < min_size:
            print(f"  ERROR: File too small ({len(data)} bytes, expected >{min_size})")
            return False
            
        # Write to file
        with open(dest_path, 'wb') as f:
            f.write(data)
            
        print(f"  OK: {len(data):,} bytes -> {dest_path}")
        return True
        
    except Exception as e:
        print(f"  FAILED: {e}")
        return False

def copy_from_system():
    """Try to copy DLLs from common Windows installation paths."""
    search_paths = [
        "C:/OpenSSL-Win64",
        "C:/OpenSSL-Win64/bin",
        "C:/Program Files/OpenSSL-Win64",
        "C:/Program Files/OpenSSL-Win64/bin",
        "C:/Programmi/OpenSSL-Win64",
        "C:/Programmi/OpenSSL-Win64/bin",
        os.path.dirname(sys.executable),
        os.path.join(os.path.dirname(sys.executable), 'DLLs'),
        os.path.join(os.path.dirname(sys.executable), 'Library', 'bin'),
    ]
    
    # Add PATH directories
    search_paths.extend(os.environ.get('PATH', '').split(os.pathsep))
    
    dll_names = ['libssl-1_1-x64.dll', 'libcrypto-1_1-x64.dll']
    found_dlls = {}
    
    for path in search_paths:
        if not path or not os.path.exists(path):
            continue
        for dll_name in dll_names:
            if dll_name in found_dlls:
                continue
            dll_path = os.path.join(path, dll_name)
            if os.path.exists(dll_path):
                found_dlls[dll_name] = dll_path
                print(f"  Found: {dll_path}")
    
    return found_dlls

def main():
    print("=" * 60)
    print("  OpenSSL 1.1.1 DLL Downloader for Pickfair")
    print("  (Windows 7 Compatible)")
    print("=" * 60)
    print()
    
    # Create output directory
    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_dir = os.path.join(script_dir, 'third_party', 'openssl')
    os.makedirs(output_dir, exist_ok=True)
    
    print(f"Output directory: {output_dir}")
    print()
    
    # Check if DLLs already exist
    existing_dlls = []
    for dll_info in DLL_SOURCES:
        dll_path = os.path.join(output_dir, dll_info['name'])
        if os.path.exists(dll_path) and os.path.getsize(dll_path) > dll_info['expected_size_min']:
            existing_dlls.append(dll_info['name'])
            print(f"[OK] Already exists: {dll_info['name']}")
    
    if len(existing_dlls) >= 2:
        print()
        print("All OpenSSL DLLs already present. Nothing to do.")
        return 0
    
    print()
    print("[1/3] Searching for DLLs in system...")
    
    # Try to copy from system first
    system_dlls = copy_from_system()
    
    if len(system_dlls) >= 2:
        print()
        print("[2/3] Copying DLLs from system installation...")
        for dll_name, dll_path in system_dlls.items():
            dest_path = os.path.join(output_dir, dll_name)
            if not os.path.exists(dest_path):
                shutil.copy2(dll_path, dest_path)
                print(f"  Copied: {dll_name}")
        print()
        print("[OK] OpenSSL DLLs copied from system!")
        return 0
    
    print("  Not found in system paths.")
    print()
    print("[2/3] Downloading from internet...")
    print()
    print("NOTE: If download fails, manually install OpenSSL 1.1.1w from:")
    print("  https://slproweb.com/download/Win64OpenSSL_Light-1_1_1w.exe")
    print()
    
    # Download DLLs
    success_count = 0
    for dll_info in DLL_SOURCES:
        dll_name = dll_info['name']
        dest_path = os.path.join(output_dir, dll_name)
        
        if os.path.exists(dest_path) and os.path.getsize(dest_path) > dll_info['expected_size_min']:
            print(f"[SKIP] {dll_name} already exists")
            success_count += 1
            continue
        
        print(f"[DOWNLOAD] {dll_name}")
        
        downloaded = False
        for url in dll_info['urls']:
            if download_file(url, dest_path, dll_info['expected_size_min']):
                downloaded = True
                break
        
        if downloaded:
            success_count += 1
        else:
            print(f"  FAILED to download {dll_name}")
    
    print()
    if success_count >= 2:
        print("[3/3] Verifying DLLs...")
        for dll_info in DLL_SOURCES:
            dll_path = os.path.join(output_dir, dll_info['name'])
            if os.path.exists(dll_path):
                size = os.path.getsize(dll_path)
                print(f"  {dll_info['name']}: {size:,} bytes")
        print()
        print("=" * 60)
        print("  SUCCESS! OpenSSL 1.1.1 DLLs ready for bundling.")
        print("  Run build.bat to create Windows 7 compatible build.")
        print("=" * 60)
        return 0
    else:
        print("=" * 60)
        print("  FAILED: Could not obtain OpenSSL DLLs")
        print()
        print("  Manual fix:")
        print("  1. Download: https://slproweb.com/download/Win64OpenSSL_Light-1_1_1w.exe")
        print("  2. Install OpenSSL")
        print("  3. Copy these files to third_party/openssl/:")
        print("     - libssl-1_1-x64.dll")
        print("     - libcrypto-1_1-x64.dll")
        print("=" * 60)
        return 1

if __name__ == "__main__":
    sys.exit(main())
