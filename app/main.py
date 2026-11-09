import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.models import Material, Profile, Result
from app.ranking import Ranker

DEMO_PATH = Path(__file__).resolve().parents[1] / "data" / "demo.json"


@asynccontextmanager
async def lifespan(app: FastAPI):
    configured = os.getenv("CATALOG_PATH")
    path = Path(configured) if configured else DEMO_PATH
    materials = [Material.model_validate(row) for row in json.loads(path.read_text(encoding="utf-8"))]
    app.state.ranker = Ranker(materials)
    app.state.source = "local" if configured else "synthetic"
    yield


app = FastAPI(title="Альфа-Курс · Рекомендации", version="1.0.0", lifespan=lifespan)
origins = [origin.strip() for origin in os.getenv("CORS_ORIGINS", "").split(",") if origin.strip()]
if origins:
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"],
                       allow_headers=["Content-Type"])


@app.get("/")
def index():
    return {"docs": "/docs", "recommendations": "/api/v1/recommendations"}


@app.get("/health")
def health(request: Request):
    return {"status": "ok", "catalog": request.app.state.source,
            "materials": len(request.app.state.ranker.materials)}


@app.get("/api/v1/onboarding")
def onboarding(request: Request):
    materials = request.app.state.ranker.materials.values()
    return {
        "optional": True,
        "topics": sorted({topic for item in materials for topic in item.topics}),
        "industries": sorted({industry for item in materials for industry in item.industries}),
        "stages": {"idea": "Идея", "launch": "Запуск", "operating": "Действующий бизнес", "growth": "Рост"},
        "levels": {"beginner": "Начинающий", "intermediate": "С опытом", "advanced": "Продвинутый"},
        "formats": sorted({item.format for item in materials}),
    }


@app.post("/api/v1/recommendations", response_model=Result)
def recommendations(profile: Profile, request: Request):
    return request.app.state.ranker.recommend(profile)
