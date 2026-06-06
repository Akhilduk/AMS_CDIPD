from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from app.core.config import SCHEDULER_ENABLED
from app.core.database import engine, Base
from app.core.middleware import ProductionSecurityMiddleware
from app.core.schema import apply_compatibility_migrations
from seed.seed_demo import seed_data
from app.routers import auth, dashboard, admin, assets, allocations, lifecycle, notifications, policies, tickets, returns, reports, production

Base.metadata.create_all(bind=engine)
apply_compatibility_migrations(engine)
seed_data()

app = FastAPI(title="CDIPD Asset Management System")
app.add_middleware(ProductionSecurityMiddleware)

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
