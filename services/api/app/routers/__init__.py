from app.routers import auth, organizations

ROUTERS = [
    auth.router,
    organizations.router,
]
