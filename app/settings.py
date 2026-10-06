import os

class Settings:
    def __init__(self):
        self.app_name = os.environ.get("APP_NAME", "VM Manager")
        self.app_host = os.environ.get("APP_HOST", "0.0.0.0")
        self.app_port = int(os.environ.get("APP_PORT", "8088"))
        self.libvirt_uri = os.environ.get("LIBVIRT_URI", "qemu:///system")
        self.novnc_web = os.environ.get("NOVNC_WEB", "/usr/share/novnc")
        self.console_proxy_host = os.environ.get("CONSOLE_PROXY_HOST", "0.0.0.0")
        self.console_proxy_base_port = int(os.environ.get("CONSOLE_PROXY_BASE_PORT", "6100"))
