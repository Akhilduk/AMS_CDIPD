from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from app.core.config import SCHEDULER_ENABLED
from app.core.database import engine, Base
from app.core.middleware import ProductionSecurityMiddleware
from app.core.schema import apply_compatibility_migrations
from seed.seed_demo import seed_data
from app.routers import auth, dashboard, admin, assets, allocations, lifecycle, notifications, policies, tickets, returns, reports, production
from app.services.helpers import redirect_with_flash

Base.metadata.create_all(bind=engine)
apply_compatibility_migrations(engine)
seed_data()

app = FastAPI(title="CDIPD Asset Management System")
app.add_middleware(ProductionSecurityMiddleware)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    accepts_html = "text/html" in request.headers.get("accept", "")
    is_form_post = request.method in {"POST", "PUT", "PATCH", "DELETE"}
    if accepts_html or is_form_post:
        target = request.headers.get("referer") or request.url.path or "/"
        if target.startswith(str(request.base_url).rstrip("/")):
            target = target.replace(str(request.base_url).rstrip("/"), "", 1) or "/"
        elif not str(target).startswith("/"):
            target = "/login" if request.url.path.startswith(("/login", "/forgot-password", "/reset-password")) else "/"
        return redirect_with_flash(str(target), "Please complete all required fields and submit the form again.", "error")
    return JSONResponse(status_code=422, content={"detail": exc.errors()})

app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.mount("/storage", StaticFiles(directory="storage"), name="storage")

app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(admin.router)
app.include_router(assets.router)
app.include_router(allocations.router)
app.include_router(lifecycle.router)
app.include_router(notifications.router)
app.include_router(policies.router)
app.include_router(tickets.router)
app.include_router(returns.router)
app.include_router(reports.router)
app.include_router(production.router)

if SCHEDULER_ENABLED:
    try:
        from app.services.scheduler import start_scheduler

        scheduler = start_scheduler()
    except Exception:
        scheduler = None
