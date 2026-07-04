from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.api import board, chat, files, git, ollama, projects, skills, sprint, state as state_routes, terminal
from backend.config import CORS_ORIGINS, FRONTEND_DIST

app = FastAPI(title="OpenHands Local Scrum Engine", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(state_routes.router)
app.include_router(projects.router)
app.include_router(skills.router)
app.include_router(board.router)
app.include_router(sprint.router)
app.include_router(chat.router)
app.include_router(files.router)
app.include_router(ollama.router)
app.include_router(terminal.router)
app.include_router(git.router)

if FRONTEND_DIST.is_dir():
    assets_dir = FRONTEND_DIST / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str):
        index = FRONTEND_DIST / "index.html"
        if index.is_file():
            return FileResponse(str(index))
        return {"detail": "Frontend not built. Run npm run build in frontend/"}

else:

    @app.get("/")
    def root_placeholder():
        return {"detail": "Frontend not built. Run npm run build in frontend/"}
