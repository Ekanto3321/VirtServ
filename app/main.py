from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.libvirt_service import LibvirtService
from app.settings import Settings

BASE_DIR = Path(__file__).resolve().parent
settings = Settings()
service = LibvirtService(settings)
app = FastAPI(title="Libvirt VM Manager")
static_dir = BASE_DIR / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

def get_domain_or_404(name: str):
    try:
        return service.get_domain(name)
    except Exception:
        raise HTTPException(status_code=404, detail="Domain not found")

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    domains = service.list_domains()
    networks = service.list_networks()
    return templates.TemplateResponse(request=request, name="index.html", context={"domains": domains, "networks": networks, "settings": settings})

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/api/domains")
def api_domains():
    return JSONResponse(service.list_domains())

@app.post("/api/domains/{name}/start")
def start_domain(name: str):
    service.start_domain(name)
    return RedirectResponse(url="/", status_code=303)

@app.post("/api/domains/{name}/shutdown")
def shutdown_domain(name: str):
    service.shutdown_domain(name)
    return RedirectResponse(url="/", status_code=303)

@app.post("/api/domains/{name}/reboot")
def reboot_domain(name: str):
    service.reboot_domain(name)
    return RedirectResponse(url="/", status_code=303)

@app.post("/api/domains/{name}/delete")
def delete_domain(name: str, remove_storage: str = Form(default="yes")):
    service.delete_domain(name, remove_storage=(remove_storage == "yes"))
    return RedirectResponse(url="/", status_code=303)

@app.get("/domains/{name}")
def domain_redirect(name: str):
    return RedirectResponse(url=f"/console/{name}", status_code=302)

@app.get("/console/{name}", response_class=HTMLResponse)
def console_page(name: str, request: Request):
    domain = get_domain_or_404(name)
    error = None
    proxy_port = None
    try:
        proxy_port = service.ensure_console_proxy(name)
    except Exception as exc:
        error = str(exc)
    return templates.TemplateResponse(request=request, name="console.html", context={"domain": domain, "settings": settings, "proxy_port": proxy_port, "error": error})

@app.get("/create-vm", response_class=HTMLResponse)
def create_vm_page(request: Request):
    networks = service.list_networks()
    iso_files = service.list_iso_files()
    return templates.TemplateResponse(request=request, name="create_vm.html", context={"settings": settings, "networks": networks, "iso_files": iso_files})

@app.post("/create-vm", response_class=HTMLResponse)
def create_vm_submit(request: Request, name: str = Form(...), network: str = Form(default="default"), memory: int = Form(default=4096), vcpus: int = Form(default=2), disk: int = Form(default=40), iso_path: str = Form(default="")):
    networks = service.list_networks()
    iso_files = service.list_iso_files()
    try:
        if not iso_path:
            raise RuntimeError("Please select an installation ISO before creating a VM.")
        output = service.create_vm(name=name, memory=memory, vcpus=vcpus, disk=disk, iso_path=iso_path, network=network)
        message = f"VM '{name}' creation started successfully. {output}"
    except Exception as exc:
        message = f"VM creation failed: {exc}"
    return templates.TemplateResponse(request=request, name="create_vm.html", context={"settings": settings, "networks": networks, "iso_files": iso_files, "message": message})

@app.get("/networks", response_class=HTMLResponse)
def networks_page(request: Request):
    domains = service.list_domains()
    networks = service.list_networks()
    return templates.TemplateResponse(request=request, name="networks.html", context={"settings": settings, "domains": domains, "networks": networks})

@app.post("/networks/create", response_class=HTMLResponse)
def create_network_submit(request: Request, name: str = Form(...), cidr: str = Form(...)):
    domains = service.list_domains()
    try:
        message = service.create_network(name=name, cidr=cidr)
        error = None
    except Exception as exc:
        message = None
        error = str(exc)
    networks = service.list_networks()
    return templates.TemplateResponse(request=request, name="networks.html", context={"settings": settings, "domains": domains, "networks": networks, "message": message, "error": error})

@app.post("/networks/{name}/delete", response_class=HTMLResponse)
def delete_network_submit(name: str):
    service.delete_network(name)
    return RedirectResponse(url="/networks", status_code=303)

@app.post("/networks/attach", response_class=HTMLResponse)
def attach_network_submit(request: Request, domain_name: str = Form(...), network_name: str = Form(...)):
    try:
        message = service.attach_network_interface(domain_name=domain_name, network_name=network_name)
        error = None
    except Exception as exc:
        message = None
        error = str(exc)
    domains = service.list_domains()
    networks = service.list_networks()
    return templates.TemplateResponse(request=request, name="networks.html", context={"settings": settings, "domains": domains, "networks": networks, "message": message, "error": error})
