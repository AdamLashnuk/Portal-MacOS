import sys
from PySide6.QtWidgets import QApplication

from app.widget import FloatingWidget
from app.utils import initialize_startup_default 
import os

os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
    "--disable-features=AutomationControlled "
    "--disable-blink-features=AutomationControlled "
    "--disable-infobars"
)

# This starts the entire app
app = QApplication(sys.argv)

# Check the registry on boot
initialize_startup_default()

# Prevents the app from terminating when hidden via the tray icon ---
app.setQuitOnLastWindowClosed(False) 

# Create and show the floating bubble
widget = FloatingWidget()
widget.show()

# Keep the app running
sys.exit(app.exec())