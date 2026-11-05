from app.routers import (
    auth,
    documents,
    fulfilment,
    imports,
    inbox,
    inventory,
    orders,
    organizations,
    parties,
    products,
    schemes,
)

ROUTERS = [
    auth.router,
    organizations.router,
    products.router,
    parties.router,
    imports.router,
    inventory.router,
    orders.router,
    fulfilment.router,
    inbox.router,
    documents.router,
    schemes.router,
]
