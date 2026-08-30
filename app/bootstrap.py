from app.main import app
from app.youtube_routes import router as youtube_router

app.include_router(youtube_router)
