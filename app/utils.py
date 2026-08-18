import sys
import os
import plistlib
from PySide6.QtCore import QSettings

if sys.platform == "win32":
    import winreg
else:
    winreg = None

REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "PortalApp"
STARTUP_INITIALIZED_KEY = "startup_preference_initialized"
MAC_LAUNCH_AGENT_LABEL = "com.portal.app"

def get_asset_path(relative_path):
    """Get absolute path to an asset, handling both dev and PyInstaller environments."""
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)

def _windows_startup_command():
    if getattr(sys, 'frozen', False):
        return f'"{sys.executable}"'

    main_script = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'main.py'))
    # Replace python.exe with pythonw.exe to hide the console.
    exec_path = sys.executable.replace("python.exe", "pythonw.exe")
    return f'"{exec_path}" "{main_script}"'


def _set_windows_startup(enabled):
    app_path = _windows_startup_command()

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
        return True
    except Exception as e:
        print(f"Failed to update registry: {e}")
        return False


def _mac_launch_agent_path():
    return os.path.expanduser(
        os.path.join("~", "Library", "LaunchAgents", f"{MAC_LAUNCH_AGENT_LABEL}.plist")
    )


def _mac_program_arguments():
    if getattr(sys, "frozen", False):
        return [os.path.abspath(sys.executable)]

    main_script = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "main.py")
    )
    return [os.path.abspath(sys.executable), main_script]


def _set_macos_startup(enabled):
    launch_agent_path = _mac_launch_agent_path()
    temporary_path = f"{launch_agent_path}.tmp-{os.getpid()}"

    try:
        if not enabled:
            try:
                os.remove(launch_agent_path)
            except FileNotFoundError:
                pass
            return True

        arguments = _mac_program_arguments()
        launch_agent = {
            "Label": MAC_LAUNCH_AGENT_LABEL,
            "ProgramArguments": arguments,
            "RunAtLoad": True,
        }
        if not getattr(sys, "frozen", False):
            launch_agent["WorkingDirectory"] = os.path.dirname(arguments[-1])

        os.makedirs(os.path.dirname(launch_agent_path), exist_ok=True)
        with open(temporary_path, "wb") as plist_file:
            plistlib.dump(launch_agent, plist_file, sort_keys=True)
        os.chmod(temporary_path, 0o644)
        os.replace(temporary_path, launch_agent_path)
        return True
    except (OSError, TypeError, ValueError) as error:
        try:
            os.remove(temporary_path)
        except FileNotFoundError:
            pass
        print(f"Failed to update macOS launch agent: {error}")
        return False


def startup_supported():
    return winreg is not None or sys.platform == "darwin"


def set_startup(enabled: bool = True):
    """Enable or disable launch at login for the current platform."""
    if winreg is not None:
        updated = _set_windows_startup(enabled)
    elif sys.platform == "darwin":
        updated = _set_macos_startup(enabled)
    else:
        return False

    if updated:
        settings = QSettings("MyLLMWidget", "Portal")
        settings.setValue(STARTUP_INITIALIZED_KEY, True)
        settings.sync()
    return updated


def check_startup_enabled() -> bool:
    """Check whether Portal is configured to launch at login."""
    if winreg is not None:
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_READ)
            winreg.QueryValueEx(key, APP_NAME)
            winreg.CloseKey(key)
            return True
        except FileNotFoundError:
            return False
    if sys.platform == "darwin":
        return os.path.isfile(_mac_launch_agent_path())
    return False

def initialize_startup_default():
    """Enable startup once, without overriding an existing user's choice."""
    if not startup_supported():
        return
    settings = QSettings("MyLLMWidget", "Portal")
    # System-wide Qt fallback keys are not Portal preferences and must not make
    # a brand-new installation look like an existing one.
    settings.setFallbacksEnabled(False)
    if settings.value(STARTUP_INITIALIZED_KEY, False, type=bool):
        return

    if settings.allKeys():
        # Existing installations predate the marker. Preserve the platform's
        # current configuration because a missing entry may be intentional.
        settings.setValue(STARTUP_INITIALIZED_KEY, True)
        settings.sync()
        return

    set_startup(True)
