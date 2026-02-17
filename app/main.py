from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(override=True)

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.agents.router import router as agents_router
from app.auth.router import router as auth_router
from app.chat.router import router as chat_router
from app.db import db

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect()
    yield
    await db.disconnect()


app = FastAPI(title="Gavigans Agent Platform", version="1.0.0", lifespan=lifespan)

app.include_router(auth_router)
app.include_router(agents_router)
app.include_router(chat_router)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/")
async def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/dashboard")
async def dashboard():
    return FileResponse(str(STATIC_DIR / "dashboard.html"))
