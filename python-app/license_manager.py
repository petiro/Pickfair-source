"""
License Manager for Pickfair
Handles hardware ID generation and license key validation
"""

import hashlib
import platform
import uuid
import os
import re

SECRET_KEY = "PickfairBetfair2024SecretKey"

def get_hardware_id():
    """Generate a unique hardware ID based on machine characteristics"""
    import subprocess
    parts = []
    
    parts.append(platform.processor())
    parts.append(platform.machine())
    parts.append(platform.system())
    parts.append(str(uuid.getnode()))
    
    try:
        result = subprocess.run(
            ['reg', 'query', r'HKLM\SOFTWARE\Microsoft\Cryptography', '/v', 'MachineGuid'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            for line in result.stdout.split('\n'):
                if 'MachineGuid' in line:
                    parts.append(line.split()[-1])
                    break
    except Exception:
        pass
    
    try:
        result = subprocess.run(
            ['wmic', 'diskdrive', 'get', 'serialnumber'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            lines = [l.strip() for l in result.stdout.split('\n') if l.strip() and 'SerialNumber' not in l]
            if lines:
                parts.append(lines[0])
    except Exception:
        pass
    
    raw_id = "|".join(parts)
    hash_obj = hashlib.sha256(raw_id.encode())
    hardware_id = hash_obj.hexdigest()[:16].upper()
    
    return f"{hardware_id[:4]}-{hardware_id[4:8]}-{hardware_id[8:12]}-{hardware_id[12:16]}"

def generate_license_key(hardware_id):
    """Generate a license key for a given hardware ID"""
    clean_hwid = hardware_id.replace("-", "")
    
    combined = f"{clean_hwid}{SECRET_KEY}"
    hash_obj = hashlib.sha256(combined.encode())
    key_hash = hash_obj.hexdigest()[:20].upper()
    
    license_key = f"PICK-{key_hash[:4]}-{key_hash[4:8]}-{key_hash[8:12]}-{key_hash[12:16]}-{key_hash[16:20]}"
    return license_key

def validate_license_key(hardware_id, license_key):
    """Validate if a license key matches the hardware ID"""
    expected_key = generate_license_key(hardware_id)
    return license_key.strip().upper() == expected_key

def get_license_file_path():
    """Get the path to the license file"""
    appdata = os.environ.get('APPDATA', os.path.expanduser('~'))
    pickfair_dir = os.path.join(appdata, 'Pickfair')
    os.makedirs(pickfair_dir, exist_ok=True)
    return os.path.join(pickfair_dir, 'license.key')

def save_license(license_key):
    """Save license key to file"""
    try:
        license_path = get_license_file_path()
        with open(license_path, 'w') as f:
            f.write(license_key.strip().upper())
        return True
    except Exception:
        return False

def load_license():
    """Load license key from file"""
    try:
        license_path = get_license_file_path()
        if os.path.exists(license_path):
            with open(license_path, 'r') as f:
                return f.read().strip()
    except Exception:
        pass
    return None

def is_licensed():
    """Check if the application is properly licensed"""
    hardware_id = get_hardware_id()
    license_key = load_license()
    
    if license_key:
        return validate_license_key(hardware_id, license_key)
    return False

def activate_license(license_key):
    """Attempt to activate with the given license key"""
    hardware_id = get_hardware_id()
    
    if validate_license_key(hardware_id, license_key):
        save_license(license_key)
        return True, "Licenza attivata con successo!"
    else:
        return False, "Chiave di licenza non valida per questo PC."

def deactivate_license():
    """Remove the license"""
    try:
        license_path = get_license_file_path()
        if os.path.exists(license_path):
            os.remove(license_path)
        return True
    except Exception:
        return False
