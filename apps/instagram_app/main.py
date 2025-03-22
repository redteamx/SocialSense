# instagram_app/main.py
from fastapi import FastAPI
from api.routes import user_routes, post_routes, analytics_routes, ml_routes, evasion_routes

app = FastAPI(title="Instagram App")
app.include_router(user_routes.router, prefix="/users")
app.include_router(post_routes.router, prefix="/posts")
app.include_router(analytics_routes.router, prefix="/analytics")
app.include_router(ml_routes.router, prefix="/ml")
app.include_router(evasion_routes.router, prefix="/evasion")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)