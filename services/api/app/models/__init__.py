"""All ORM models. Importing this package registers every table on `Base.metadata`."""

from app.models.finance import (  # noqa: F401
    CreditNote,
    Payment,
    PaymentAllocation,
    PurchaseInvoice,
    PurchaseInvoiceLine,
    ReconciliationCase,
    ReconciliationEvent,
    ReconciliationMatch,
    SalesInvoice,
    SalesInvoiceLine,
)
from app.models.identity import (  # noqa: F401
    DataSharingGrant,
    Invitation,
    Membership,
    Organization,
    PasswordResetToken,
    RefreshToken,
    User,
)
from app.models.inventory import (  # noqa: F401
    Batch,
    GoodsReceipt,
    GoodsReceiptLine,
    InventoryBalance,
    InventoryMovement,
    StockCount,
    StockCountLine,
)
from app.models.master import (  # noqa: F401
    PriceList,
    PriceListItem,
    Product,
    Retailer,
    Supplier,
    UnitConversion,
    UnitOfMeasure,
    Warehouse,
)
from app.models.orders import (  # noqa: F401
    Dispatch,
    Document,
    Extraction,
    Order,
    OrderLine,
    OrderRevision,
    OrderSource,
    OrderStatusEvent,
    PickList,
    PickListLine,
    ReturnLine,
    ReturnRecord,
)
from app.models.platform import (  # noqa: F401
    AuditEvent,
    Comment,
    ExceptionItem,
    IdempotencyKey,
    ImportBatch,
    IntegrationConnection,
    IntegrationSyncRun,
    Job,
)
from app.models.schemes import Scheme, SchemeApplication, SchemeClaim, SchemeVersion  # noqa: F401
