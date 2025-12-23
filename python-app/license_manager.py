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
    try:
        mac = uuid.getnode()
        machine = platform.machine()
        processor = platform.processor()
        system = platform.system()
        
        raw_id = f"{mac}-{machine}-{processor}-{system}"
        
        hash_obj = hashlib.sha256(raw_id.encode())
        hardware_id = hash_obj.hexdigest()[:16].upper()
        
        formatted = f"{hardware_id[:4]}-{hardware_id[4:8]}-{hardware_id[8:12]}-{hardware_id[12:16]}"
        return formatted
    except Exception:
        fallback = hashlib.sha256(str(uuid.getnode()).encode()).hexdigest()[:16].upper()
        return f"{fallback[:4]}-{fallback[4:8]}-{fallback[8:12]}-{fallback[12:16]}"

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
