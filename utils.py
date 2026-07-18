import sys
import os

if sys.platform == "win32":
    import winreg
else:
    winreg = None

REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "PortalApp"

def get_asset_path(relative_path):
    """Get absolute path to an asset, handling both dev and PyInstaller environments."""
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)

def set_startup(enabled: bool = True):
    """Enables or disables launching the app on Windows startup."""
    if not winreg: return
        
    if getattr(sys, 'frozen', False):
        app_path = f'"{sys.executable}"'
    else:
        main_script = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'main.py'))
        # Replace python.exe with pythonw.exe to hide the console
        exec_path = sys.executable.replace("python.exe", "pythonw.exe")
        app_path = f'"{exec_path}" "{main_script}"'

    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_SET_VALUE)
        if enabled:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, app_path)
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
    except Exception as e:
        print(f"Failed to update registry: {e}")

def check_startup_enabled() -> bool:
    """Checks if the app is currently set to run on startup."""
    if not winreg: return False
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_READ)
        winreg.QueryValueEx(key, APP_NAME)
        winreg.CloseKey(key)
        return True
    except FileNotFoundError:
        return False

def initialize_startup_default():
    """Checks if it's the first run and enables startup by default."""
    if not winreg: return
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_READ)
        try:
            winreg.QueryValueEx(key, APP_NAME)
        except FileNotFoundError:
            # Key doesn't exist yet, so write it to make it ON by default
            set_startup(True)
        winreg.CloseKey(key)
    except Exception:
        pass