from app.routers import auth, imports, organizations, parties, products

ROUTERS = [
    auth.router,
    organizations.router,
    products.router,
    parties.router,
    imports.router,
]
