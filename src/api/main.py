import time
import uuid
import logging
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.config import API_CORS_ORIGINS
from src.api.routes.health import router as health_router
from src.api.routes.query import router as query_router
from src.api.routes.search import router as search_router
from src.api.routes.documents import router as documents_router

# Configure logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ashen_api")

app = FastAPI(
    title="Ashen Era Archive Intelligence API",
    description=(
        "Evidence-grounded document intelligence API for the Ashen Era Archive. "
        "Supports hybrid retrieval, multi-hop knowledge graph reasoning (Track 1B), "
        "multimodal asset extraction (Track 1A), and iterative search traces (Track 1C)."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# 1. CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=API_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Process-Time", "X-Request-ID"],
)


# 2. Timing and Request ID Middleware
@app.middleware("http")
async def add_process_time_and_id_header(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    start_time = time.time()

    try:
        response: Response = await call_next(request)
    except Exception as exc:
        logger.exception(f"Unhandled exception during request {request_id}: {exc}")
        response = JSONResponse(
            status_code=500,
            content={"detail": "Internal server error occurred while processing request."},
        )

    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = f"{process_time:.4f}s"
    response.headers["X-Request-ID"] = request_id
    return response


# 3. Register Routers
app.include_router(health_router)
app.include_router(query_router)
app.include_router(search_router)
app.include_router(documents_router)


# 4. Root Welcome Route
@app.get("/", tags=["Root"])
def root():
    """Welcome endpoint providing archive metadata and documentation links."""
    return {
        "service": "Ashen Era Archive Intelligence API",
        "version": "1.0.0",
        "status": "online",
        "docs": "/docs",
        "health": "/health",
        "tracks_supported": [
            "1A: Rich Multimodal Answers & Figure Plate OCR",
            "1B: Cross-Document Multi-Hop Knowledge Graph Reasoning",
            "1C: Iterative Agentic Search & Observability Traces",
        ],
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.api.main:app", host="0.0.0.0", port=8000, reload=True)
