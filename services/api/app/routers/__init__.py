from app.routers import auth, fulfilment, imports, inventory, orders, organizations, parties, products

ROUTERS = [
    auth.router,
    organizations.router,
    products.router,
    parties.router,
    imports.router,
    inventory.router,
    orders.router,
    fulfilment.router,
]
