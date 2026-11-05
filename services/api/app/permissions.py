"""Central role -> permission mapping. Server-side checks use `has_permission`."""

ALL_PERMISSIONS: tuple[str, ...] = (
    "org.read",
    "org.manage",
    "users.manage",
    "products.read",
    "products.write",
    "retailers.read",
    "retailers.write",
    "suppliers.read",
    "suppliers.write",
    "warehouses.manage",
    "pricing.read",
    "pricing.write",
    "orders.read",
    "orders.create",
    "orders.approve",
    "orders.cancel",
    "orders.override",
    "inventory.read",
    "inventory.adjust",
    "inventory.reserve",
    "fulfilment.read",
    "fulfilment.execute",
    "schemes.read",
    "schemes.manage",
    "schemes.approve",
    "finance.read",
    "finance.write",
    "finance.reconcile",
    "finance.approve",
    "payments.read",
    "payments.allocate",
    "exceptions.read",
    "exceptions.manage",
    "reports.read",
    "reports.export",
    "audit.read",
    "integrations.manage",
    "data_sharing.manage",
    "imports.run",
    "shared_data.read",
)

_owner = set(ALL_PERMISSIONS) - {"shared_data.read"}

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "org_owner": frozenset(_owner),
    "org_admin": frozenset(_owner - {"data_sharing.manage"}),
    "operations_manager": frozenset(
        {
            "org.read",
            "products.read",
            "products.write",
            "retailers.read",
            "retailers.write",
            "suppliers.read",
            "pricing.read",
            "orders.read",
            "orders.create",
            "orders.approve",
            "orders.cancel",
            "orders.override",
            "inventory.read",
            "inventory.adjust",
            "inventory.reserve",
            "fulfilment.read",
            "fulfilment.execute",
            "schemes.read",
            "exceptions.read",
            "exceptions.manage",
            "reports.read",
            "imports.run",
        }
    ),
    "accountant": frozenset(
        {
            "org.read",
            "products.read",
            "retailers.read",
            "suppliers.read",
            "suppliers.write",
            "pricing.read",
            "orders.read",
            "inventory.read",
            "fulfilment.read",
            "schemes.read",
            "schemes.manage",
            "finance.read",
            "finance.write",
            "finance.reconcile",
            "finance.approve",
            "payments.read",
            "payments.allocate",
            "exceptions.read",
            "exceptions.manage",
            "reports.read",
            "reports.export",
            "audit.read",
            "imports.run",
        }
    ),
    "sales_rep": frozenset(
        {
            "org.read",
            "products.read",
            "retailers.read",
            "pricing.read",
            "orders.read",
            "orders.create",
            "inventory.read",
            "schemes.read",
        }
    ),
    "warehouse_operator": frozenset(
        {
            "org.read",
            "products.read",
            "orders.read",
            "inventory.read",
            "inventory.adjust",
            "fulfilment.read",
            "fulfilment.execute",
            "exceptions.read",
        }
    ),
}

# platform_admin is a user flag (User.is_platform_admin), not an org membership role;
# it grants only the platform endpoints, never implicit access to tenant data.
ROLES = tuple(ROLE_PERMISSIONS.keys()) + ("manufacturer_manager", "manufacturer_viewer")
DISTRIBUTOR_ROLES = (
    "org_owner",
    "org_admin",
    "operations_manager",
    "accountant",
    "sales_rep",
    "warehouse_operator",
)
MANUFACTURER_ROLES = ("org_owner", "manufacturer_manager", "manufacturer_viewer")


# A manufacturer org only ever sees data explicitly shared with it, plus its own admin.
MANUFACTURER_ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "org_owner": frozenset({"org.read", "org.manage", "users.manage", "shared_data.read", "audit.read"}),
    "org_admin": frozenset({"org.read", "users.manage", "shared_data.read", "audit.read"}),
    "manufacturer_manager": frozenset({"org.read", "users.manage", "shared_data.read"}),
    "manufacturer_viewer": frozenset({"org.read", "shared_data.read"}),
}


def permissions_for(role: str, org_type: str = "distributor") -> frozenset[str]:
    if org_type == "manufacturer":
        return MANUFACTURER_ROLE_PERMISSIONS.get(role, frozenset())
    if role in ("manufacturer_manager", "manufacturer_viewer"):
        return frozenset()
    return ROLE_PERMISSIONS.get(role, frozenset())


def has_permission(role: str, permission: str, org_type: str = "distributor") -> bool:
    return permission in permissions_for(role, org_type)
