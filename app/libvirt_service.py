from __future__ import annotations

import ipaddress
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

import libvirt


class LibvirtService:
    def __init__(self, settings):
        self.settings = settings
        self.proxy_dir = Path("/tmp/libvirt-vm-manager-proxies")
        self.proxy_dir.mkdir(parents=True, exist_ok=True)

    def connect(self):
        conn = libvirt.open(self.settings.libvirt_uri)
        if conn is None:
            raise RuntimeError(f"Failed to connect to libvirt URI: {self.settings.libvirt_uri}")
        return conn

    def _state_label(self, state: int) -> str:
        mapping = {
            libvirt.VIR_DOMAIN_NOSTATE: "nostate",
            libvirt.VIR_DOMAIN_RUNNING: "running",
            libvirt.VIR_DOMAIN_BLOCKED: "blocked",
            libvirt.VIR_DOMAIN_PAUSED: "paused",
            libvirt.VIR_DOMAIN_SHUTDOWN: "shutdown",
            libvirt.VIR_DOMAIN_SHUTOFF: "shutoff",
            libvirt.VIR_DOMAIN_CRASHED: "crashed",
            libvirt.VIR_DOMAIN_PMSUSPENDED: "suspended",
        }
        return mapping.get(state, "unknown")

    def _domain_networks(self, domain) -> list[str]:
        try:
            xml = domain.XMLDesc(0)
        except Exception:
            return []
        return re.findall(r"<source network='([^']+)'", xml)

    def _domain_to_dict(self, domain) -> dict[str, Any]:
        info = domain.info()
        memory_mb = int(info[1] / 1024) if info and len(info) > 1 else 0
        vcpus = int(info[3]) if info and len(info) > 3 else 0
        return {
            "id": domain.ID() if domain.ID() >= 0 else None,
            "name": domain.name(),
            "uuid": domain.UUIDString(),
            "state": self._state_label(info[0]),
            "memory_mb": memory_mb,
            "vcpus": vcpus,
            "autostart": bool(domain.autostart()),
            "is_active": bool(domain.isActive()),
            "networks": self._domain_networks(domain),
        }

    def list_domains(self) -> list[dict[str, Any]]:
        conn = self.connect()
        try:
            domains = conn.listAllDomains()
            items = [self._domain_to_dict(d) for d in domains]
            items.sort(key=lambda x: x["name"].lower())
            return items
        finally:
            conn.close()

    def get_domain(self, name: str) -> dict[str, Any]:
        conn = self.connect()
        try:
            return self._domain_to_dict(conn.lookupByName(name))
        finally:
            conn.close()

    def start_domain(self, name: str) -> None:
        conn = self.connect()
        try:
            domain = conn.lookupByName(name)
            if not domain.isActive():
                domain.create()
        finally:
            conn.close()

    def shutdown_domain(self, name: str) -> None:
        conn = self.connect()
        try:
            domain = conn.lookupByName(name)
            if domain.isActive():
                domain.shutdown()
        finally:
            conn.close()

    def reboot_domain(self, name: str) -> None:
        conn = self.connect()
        try:
            domain = conn.lookupByName(name)
            if domain.isActive():
                domain.reboot()
            else:
                domain.create()
        finally:
            conn.close()

    def delete_domain(self, name: str, remove_storage: bool = True) -> None:
        subprocess.run(["virsh", "-c", self.settings.libvirt_uri, "destroy", name], check=False, capture_output=True, text=True)
        cmd = ["virsh", "-c", self.settings.libvirt_uri, "undefine", name]
        if remove_storage:
            cmd.extend(["--remove-all-storage", "--nvram"])
        result = subprocess.run(cmd, check=False, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "Failed to delete VM").strip())

    def _collect_iso_files_from_dir(self, folder: Path) -> list[dict[str, str]]:
        results = []
        try:
            if not folder.exists() or not folder.is_dir():
                return results
            for item in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
                try:
                    if item.is_file() and item.suffix.lower() == ".iso":
                        results.append({"name": item.name, "path": str(item.resolve())})
                except (PermissionError, OSError):
                    continue
        except (PermissionError, OSError):
            return results
        return results

    def list_iso_files(self) -> list[dict[str, str]]:
        iso_dirs = [
            Path.home() / "isos",
            Path.home() / "Downloads",
            Path("/var/lib/libvirt/boot"),
            Path("/var/lib/libvirt/images"),
            Path("/var/lib/libvirt/images/iso"),
            Path("/var/lib/libvirt/images/isos"),
            Path("/var/lib/libvirt/isos"),
        ]
        found, seen = [], set()
        for folder in iso_dirs:
            for entry in self._collect_iso_files_from_dir(folder):
                if entry["path"] not in seen:
                    seen.add(entry["path"])
                    found.append(entry)
            try:
                if folder.exists() and folder.is_dir():
                    for child in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
                        try:
                            if not child.is_dir():
                                continue
                            for entry in self._collect_iso_files_from_dir(child):
                                if entry["path"] not in seen:
                                    seen.add(entry["path"])
                                    found.append(entry)
                        except (PermissionError, OSError):
                            continue
            except (PermissionError, OSError):
                continue
        found.sort(key=lambda x: x["name"].lower())
        return found

    def list_networks(self) -> list[dict[str, Any]]:
        conn = self.connect()
        try:
            nets = []
            for net in conn.listAllNetworks():
                xml = net.XMLDesc(0)
                bridge = re.search(r"<bridge name='([^']+)'", xml)
                ip_block = re.search(r"<ip address='([^']+)' netmask='([^']+)'", xml)
                cidr = ""
                if ip_block:
                    iface = ipaddress.ip_interface(f"{ip_block.group(1)}/{ip_block.group(2)}")
                    cidr = str(iface.network)
                nets.append({
                    "name": net.name(),
                    "active": bool(net.isActive()),
                    "autostart": bool(net.autostart()),
                    "bridge": bridge.group(1) if bridge else "",
                    "cidr": cidr,
                })
            nets.sort(key=lambda x: x["name"].lower())
            return nets
        finally:
            conn.close()

    def create_network(self, name: str, cidr: str) -> str:
        network = ipaddress.ip_network(cidr, strict=False)
        if network.version != 4:
            raise RuntimeError("Only IPv4 CIDR ranges are supported right now.")
        if network.prefixlen > 28 or network.prefixlen < 16:
            raise RuntimeError("Use a subnet between /16 and /28.")
        hosts = list(network.hosts())
        if len(hosts) < 4:
            raise RuntimeError("Subnet is too small to allocate gateway and DHCP range.")
        gateway = str(hosts[0])
        dhcp_start = str(hosts[1])
        dhcp_end = str(hosts[-2])
        netmask = str(network.netmask)
        bridge = f"virbr-{name[:10]}"
        xml = f"""<network>
  <name>{name}</name>
  <forward mode='nat'/>
  <bridge name='{bridge}' stp='on' delay='0'/>
  <ip address='{gateway}' netmask='{netmask}'>
    <dhcp>
      <range start='{dhcp_start}' end='{dhcp_end}'/>
    </dhcp>
  </ip>
</network>"""
        define = subprocess.run(["virsh", "-c", self.settings.libvirt_uri, "net-define", "/dev/stdin"], input=xml, text=True, capture_output=True)
        if define.returncode != 0:
            raise RuntimeError((define.stderr or define.stdout or "Failed to define network").strip())
        for cmd in (["net-autostart", name], ["net-start", name]):
            result = subprocess.run(["virsh", "-c", self.settings.libvirt_uri, *cmd], check=False, capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError((result.stderr or result.stdout or f"Failed to run {' '.join(cmd)}").strip())
        return f"Network '{name}' created with CIDR {network}."

    def delete_network(self, name: str) -> None:
        subprocess.run(["virsh", "-c", self.settings.libvirt_uri, "net-destroy", name], check=False, capture_output=True, text=True)
        result = subprocess.run(["virsh", "-c", self.settings.libvirt_uri, "net-undefine", name], check=False, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "Failed to delete network").strip())

    def attach_network_interface(self, domain_name: str, network_name: str) -> str:
        xml = f"<interface type='network'><source network='{network_name}'/><model type='virtio'/></interface>"
        result = subprocess.run([
            "virsh", "-c", self.settings.libvirt_uri, "attach-device", domain_name, "/dev/stdin", "--config", "--live"
        ], input=xml, text=True, capture_output=True)
        if result.returncode != 0:
            result = subprocess.run([
                "virsh", "-c", self.settings.libvirt_uri, "attach-device", domain_name, "/dev/stdin", "--config"
            ], input=xml, text=True, capture_output=True)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "Failed to attach interface").strip())
        return f"Attached network '{network_name}' to VM '{domain_name}'."

    def create_vm(
        self,
        name: str,
        memory: int,
        vcpus: int,
        disk: int,
        iso_path: str,
        network: str | None = None,
        osinfo: str = "linux2024",
    ) -> str:
        cmd = [
            "virt-install",
            "--connect", self.settings.libvirt_uri,
            "--name", name,
            "--memory", str(memory),
            "--vcpus", str(vcpus),
            "--disk", f"size={disk},format=qcow2,bus=sata",
            "--cdrom", iso_path,
            "--network", f"network={network or 'default'}",
            "--osinfo", osinfo,
            "--graphics", "vnc,listen=127.0.0.1",
            "--noautoconsole",
        ]
        result = subprocess.run(cmd, check=False, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "virt-install failed").strip())
        return (result.stdout or result.stderr or "VM creation started").strip()

    def get_display_uri(self, name: str) -> str:
        result = subprocess.run(["virsh", "-c", self.settings.libvirt_uri, "domdisplay", name], check=False, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "Unable to get domdisplay").strip())
        uri = (result.stdout or "").strip()
        if not uri:
            raise RuntimeError("No display URI returned by virsh domdisplay")
        return uri

    def parse_display(self, uri: str) -> tuple[str, int]:
        if uri.startswith("vnc://"):
            body = uri[len("vnc://"):]
            host, display = body.rsplit(":", 1)
            return host or "127.0.0.1", 5900 + int(display)
        m = re.match(r"^(?:spice|vnc)://([^:]+):(\\d+)$", uri)
        if m:
            return m.group(1), int(m.group(2))
        raise RuntimeError(f"Unsupported display URI: {uri}")

    def _is_port_open(self, host: str, port: int) -> bool:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            return False

    def ensure_console_proxy(self, name: str) -> int:
        websockify = shutil.which("websockify")
        if not websockify:
            raise RuntimeError("websockify is not installed")

        port = self.settings.console_proxy_base_port

        if self._is_port_open("127.0.0.1", port):
            return port

        uri = self.get_display_uri(name)
        target_host, target_port = self.parse_display(uri)

        log_file = self.proxy_dir / f"proxy-{port}.log"
        cmd = [
            websockify,
            "--web", self.settings.novnc_web,
            f"{self.settings.console_proxy_host}:{port}",
            f"{target_host}:{target_port}",
        ]
        with open(log_file, "ab") as lf:
            subprocess.Popen(cmd, stdout=lf, stderr=lf)

        time.sleep(1)
        if not self._is_port_open("127.0.0.1", port):
            raise RuntimeError(f"Failed to start console proxy on {port}. See {log_file}")

        return port
