from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse

from app import models  # noqa: F401  (registra los modelos en Base.metadata)
from app.core.deps import NoAutenticado
from app.database import Base, engine
from app.routers import web


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="Falcon PDB", lifespan=lifespan)


@app.exception_handler(NoAutenticado)
async def no_autenticado(request: Request, exc: NoAutenticado):
    return RedirectResponse("/login", status_code=303)


app.include_router(web.router)
