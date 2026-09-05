from app.main import app
from app.youtube_routes import router as youtube_router
from app.google_cloud_test_user_routes import router as google_cloud_test_user_router

# Register the Cloud repair callback before the regular YouTube callback. It
# delegates every non-repair OAuth state to the existing YouTube handler.
app.include_router(google_cloud_test_user_router)
app.include_router(youtube_router)
