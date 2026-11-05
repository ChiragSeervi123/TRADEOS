# TRADEOS — Master Product & Engineering Specification

**Document:** `SPEC.md`  
**Version:** 1.0  
**Status:** Build specification  
**Product:** TRADEOS  
**Market:** India-first FMCG distribution  
**Primary customer:** Independent and mid-sized FMCG distributors  
**Secondary customer:** FMCG manufacturers  
**Product ambition:** Commercially viable, multi-tenant B2B SaaS

---

## 0. Instructions to the autonomous engineering agent

You are the principal product engineer, software architect, full-stack developer, AI engineer, QA engineer, and DevOps engineer responsible for implementing TRADEOS from this specification.

Your goal is to build the most complete, reliable, demonstrable product that can be delivered in the current repository and environment. Work autonomously through the implementation plan. Do not stop after producing plans or mockups. Inspect the repository, make decisions consistent with this specification, implement the product, run it, test it, fix defects, and document what is genuinely complete.

### 0.1 Execution rules

1. Inspect the existing repository before changing anything. Preserve useful existing work. Do not delete or replace a working project merely to impose a preferred structure.
2. If the repository is empty, scaffold the application using the architecture below.
3. Create and maintain `CLAUDE.md`, `docs/`, `README.md`, `.env.example`, migrations, tests, and a progress log.
4. Work in small, coherent phases. After each phase: run relevant tests, inspect the diff, fix regressions, update documentation, and record completion in `docs/BUILD_PROGRESS.md`.
5. Continue automatically to the next phase when its dependencies and acceptance criteria are satisfied. Do not ask the user to approve routine implementation decisions.
6. Ask the user only when a decision requires unavailable credentials, paid services, legally meaningful consent, access to real customer data, irreversible external action, or a product decision that cannot safely be inferred.
7. Do not claim a feature is complete unless it works end-to-end and has tests or a documented manual verification.
8. Do not leave core flows as decorative UI, hard-coded dashboard numbers, fake buttons, TODO-only handlers, or static mock responses.
9. Use synthetic data for local development and demos. Label it clearly as demo data. Never represent synthetic records as actual customer or market data.
10. If an external integration cannot be configured, implement a documented adapter interface and a working local simulator. Clearly label the integration as `simulated` or `not configured`; never imply it is connected.
11. Prefer a working, maintainable modular monolith over premature microservices.
12. Avoid unnecessary dependencies, abstractions, agents, and framework changes. Keep the implementation focused.
13. Never expose secrets, commit credentials, bypass authorization, or weaken security to make a demo work.
14. Never run destructive database commands against a non-test environment. Use migrations and explicit safeguards.
15. Do not send WhatsApp messages, emails, payment requests, claims, invoices, or other external communications without an explicit user action and configured integration.
16. At completion, provide a truthful summary: implemented features, setup steps, tests run and results, limitations, external credentials still required, and recommended next steps.

### 0.2 Definition of “complete”

The project is complete for this build when:

- A developer can start it locally from documented instructions.
- A demo organization can be created and seeded.
- A user can sign in and use role-appropriate workflows.
- Product, retailer, inventory, pricing, scheme, order, invoice, payment, return, and reconciliation workflows function.
- AI-assisted document/order extraction has a working provider path plus a deterministic demo/fallback path.
- Every financial calculation is deterministic and tested.
- Every inventory and financial mutation is auditable.
- Tenant isolation and authorization have automated tests.
- The application has useful dashboards derived from database records, not hard-coded figures.
- Critical user journeys pass end-to-end tests.
- No critical workflow is represented by a fake control.
- Documentation accurately describes what is real, simulated, or not configured.

This definition does **not** mean the product is validated for commercial production, legally certified, connected to customer ERPs, or proven to generate savings. Those require real-world pilots, security review, customer agreements, and production operations.

---

# 1. Product vision

## 1.1 One-line description

TRADEOS is an AI-assisted trade operations and financial intelligence platform that helps FMCG distributors capture orders, validate transactions, manage inventory, reconcile commercial records, and identify operational exceptions—with a future collaboration layer for manufacturers.

## 1.2 Product positioning

TRADEOS is not intended to replace a distributor’s accounting package or a manufacturer’s ERP.

It is an intelligence and workflow layer that connects fragmented trade operations and helps users act on verified business information.

## 1.3 Core value proposition

For distributors:

- Reduce repetitive order-entry work.
- Reduce order errors and exception-resolution time.
- Improve visibility into stock, receivables, returns, and schemes.
- Reconcile supplier and customer records.
- Identify possible missing credits, incorrect discounts, and unmatched transactions.
- Maintain an auditable operational history.

For manufacturers:

- Obtain authorized visibility into participating distributors’ agreed metrics.
- Monitor scheme execution and reconciliation status.
- Identify territory or distributor-level exceptions.
- Improve trade collaboration without unrestricted access to distributor data.

## 1.4 Initial product wedge

Build a distributor-first product around:

1. Master data and operational foundation.
2. Order capture and validation.
3. Inventory and pricing checks.
4. Trade scheme calculation.
5. Invoice and credit-note reconciliation.
6. Exception management and evidence.

Manufacturer functionality is a later, permissioned extension—not a blocker for the distributor MVP.

## 1.5 Product principles

- Business correctness before AI novelty.
- Human control over consequential decisions.
- Deterministic calculations for money, stock, eligibility, and credit.
- Explainable AI output with source evidence.
- Data ownership and least-privilege access.
- Auditability by design.
- Integration before replacement.
- Mobile-friendly workflows for field and warehouse users.
- Useful defaults and clear exception queues.
- No fabricated performance claims.

---

# 2. Users, organizations, and permissions

## 2.1 Organization types

Support these organization types in the data model:

- `platform`: TRADEOS platform operator.
- `distributor`: Distributor business using the core product.
- `manufacturer`: FMCG manufacturer participating in authorized collaboration.

The first release must be fully usable by a distributor organization without any manufacturer account.

## 2.2 User personas

### Distributor owner
Needs business-level visibility, financial exception summaries, receivables, inventory value, and operational performance. Can manage organization settings and approve sensitive actions.

### Operations manager
Needs order queues, stock availability, returns, dispatch status, and exception resolution.

### Accountant
Needs purchase invoices, sales records, credit notes, payment allocations, scheme reconciliation, and audit evidence.

### Sales representative
Needs retailer lookup, order creation, product availability, and order status. Must not see unrelated financial data.

### Warehouse operator
Needs pick lists, stock movements, batch/expiry details, and dispatch updates. Must not modify prices or financial records.

### Manufacturer manager
In a later phase, sees only distributor data explicitly shared with that manufacturer, under agreed scope and permissions.

## 2.3 Roles and permissions

Implement role-based access control with permission checks on the server. Hiding a button is not authorization.

Suggested roles:

- `platform_admin`
- `org_owner`
- `org_admin`
- `operations_manager`
- `accountant`
- `sales_rep`
- `warehouse_operator`
- `manufacturer_viewer`
- `manufacturer_manager`

Permissions should be granular, for example:

- `products.read`, `products.write`
- `retailers.read`, `retailers.write`
- `orders.read`, `orders.create`, `orders.approve`, `orders.cancel`
- `inventory.read`, `inventory.adjust`, `inventory.reserve`
- `schemes.read`, `schemes.manage`
- `finance.read`, `finance.reconcile`, `finance.approve`
- `payments.read`, `payments.allocate`
- `reports.read`
- `users.manage`
- `integrations.manage`
- `data_sharing.manage`

Define role-to-permission mappings centrally and test them.

## 2.4 Tenant isolation

Every tenant-owned business record must have an `organization_id` or an equally strong tenant ownership path.

Requirements:

- Derive tenant context from the authenticated session/token, not a client-supplied organization ID alone.
- Validate tenant ownership on every read and write.
- Use PostgreSQL row-level security if practical, alongside application-level authorization; do not rely on frontend filtering.
- Add tests proving one tenant cannot access another tenant’s records by changing IDs, query parameters, request bodies, or URLs.
- Platform administration must be explicit, restricted, and audited.
- Manufacturer access must use explicit data-sharing grants and field-level scope.

---

# 3. Product scope and release boundaries

## 3.1 MVP — must implement

1. Authentication and organization onboarding.
2. Role-based access control.
3. Product catalogue and SKU variants.
4. Units of measure and conversion factors.
5. Retailer/customer directory.
6. Supplier/manufacturer directory.
7. Warehouse and inventory ledger.
8. Price lists.
9. Trade scheme rules and deterministic calculations.
10. Manual order creation.
11. AI-assisted text/PDF/image order extraction with human review.
12. Order validation and approval.
13. Purchase invoice/document upload and extraction.
14. Financial record matching and reconciliation cases.
15. Credit notes and returns.
16. Receivables and payment allocation.
17. Exception queue.
18. Operational and financial dashboards.
19. Audit log.
20. CSV import/export and demo data seeding.
21. Docker-based local development and automated tests.

## 3.2 Later release — design for extension, do not let it block MVP

- WhatsApp Business API integration.
- Voice-note transcription and multilingual support.
- Manufacturer portal and network analytics.
- ERP connectors for Tally, Busy, Marg, and other systems.
- Advanced demand forecasting.
- Route optimization.
- Predictive credit-risk models.
- Advanced anomaly detection.
- Automated claim submission.
- Native mobile applications.

## 3.3 Explicitly out of scope

- Replacing a complete ERP/accounting system.
- Filing GST returns or providing tax/legal advice.
- Autonomous payment execution.
- Autonomous credit approval.
- Autonomous supplier claims or external communications.
- Manufacturing production planning.
- Consumer e-commerce.
- Nationwide logistics marketplace.
- Unsupported claims of integration or regulatory certification.

---

# 4. Core business workflows

## 4.1 Organization onboarding

1. User registers or accepts an invitation.
2. User creates an organization.
3. Organization type and basic details are saved.
4. Owner account is created.
5. Owner configures warehouses, units, product catalogue, retailers, suppliers, and opening stock.
6. User can import CSV templates or use demo seed data.
7. Onboarding checklist shows remaining setup tasks.

Provide a demo organization that can be loaded locally without external credentials.

## 4.2 Master data workflow

Product fields:

- SKU code, unique within organization.
- Product name.
- Brand.
- Category.
- Variant/pack size.
- Base unit.
- Tax classification as configurable metadata, not tax advice.
- Active/inactive status.
- Barcode/GTIN where available.
- Optional product image.
- Created/updated timestamps.

Unit of measure:

- Unit code and display name.
- Base-unit relationship.
- Conversion factor.
- Conversion validity/effective date where needed.

Retailer fields:

- Retailer code.
- Business name.
- Contact details.
- Billing and delivery addresses.
- Territory/route.
- Credit limit.
- Payment terms.
- Active/inactive status.
- External system identifiers.

Supplier fields:

- Supplier code.
- Name and contact details.
- Address.
- External system identifier.
- Active/inactive status.

Prevent accidental duplicate master records. Offer possible duplicate suggestions; do not silently merge records.

## 4.3 Order capture and processing

Supported MVP inputs:

- Manual entry.
- Pasted text.
- Uploaded PDF.
- Uploaded image.

Audio and WhatsApp are extension points unless real credentials and integrations are configured.

Order lifecycle:

`received → extracting → needs_review → draft → validated → approved → picking → dispatched → delivered`

Additional terminal or exception states:

`rejected`, `cancelled`, `partially_fulfilled`, `failed`

Requirements:

- Preserve original source text/document and metadata.
- Extract retailer, products, quantities, units, requested date, and notes.
- Resolve retailer and SKU candidates against tenant master data.
- Show candidate matches and confidence per field/line.
- Never silently select an ambiguous SKU.
- Normalize quantities into base units using configured conversion factors.
- Validate active products, customer status, price, stock, scheme, and credit exposure.
- Show a clear exception list.
- Allow authorized user correction.
- Store both extracted values and user-corrected values with provenance.
- Approval is a separate action from extraction.
- Confirmed order creation must be idempotent.
- Maintain order revision history.
- Never modify a confirmed order without a controlled amendment/cancellation workflow.

AI extraction schema should be strict and versioned. Example:

```json
{
  "schema_version": "1.0",
  "retailer_candidate": {
    "name": "Sharma General Store",
    "confidence": 0.91
  },
  "lines": [
    {
      "source_description": "surf excel 1kg",
      "sku_candidates": [
        {"sku": "SKU-001", "confidence": 0.94}
      ],
      "quantity": 5,
      "unit": "piece",
      "confidence": 0.93
    }
  ],
  "requested_delivery_date": null,
  "notes": [],
  "warnings": []
}
```

The values above are illustrative only. Validate all output against a schema and master data.

## 4.4 Pricing and scheme engine

Pricing and schemes must be deterministic, versioned, testable, and explainable.

Support at least:

- Percentage discount.
- Fixed amount discount.
- Buy X get Y.
- Quantity slab.
- Order-value slab.
- Product combination.
- Retailer-specific eligibility.
- Date-bounded schemes.
- Product/brand/category scope.
- Explicit exclusions.
- Stacking and precedence configuration.

Every scheme must include:

- Unique ID and version.
- Organization owner.
- Source document/reference.
- Effective start and end.
- Eligible products/customers.
- Qualification conditions.
- Benefit formula.
- Stacking policy.
- Maximum benefit/cap where applicable.
- Approval status.
- Created/approved by and timestamps.

Do not invent scheme rules from ambiguous documents. Store extracted candidate rules as drafts pending authorized review.

Calculations must use decimal arithmetic, explicit rounding rules, and currency-aware formatting. Avoid floating-point arithmetic for money.

For every applied scheme, persist an explanation of the qualifying quantities, threshold, rate, calculation, and resulting benefit.

## 4.5 Inventory management

Use an append-only stock movement ledger as the source of truth. Do not treat a mutable `quantity` field as the only record of inventory.

Movement types:

- Opening balance.
- Purchase receipt.
- Sales reservation.
- Reservation release.
- Dispatch.
- Customer return.
- Supplier return.
- Damage/write-off.
- Manual adjustment.
- Warehouse transfer.
- Stock count adjustment.

Track:

- Organization.
- Warehouse.
- SKU.
- Batch/lot where applicable.
- Expiry date where applicable.
- Quantity and base unit.
- Movement type.
- Source transaction.
- Actor.
- Timestamp.
- Reason and evidence.

Derived balances:

`on_hand = sum(posted stock movements)`

`available = on_hand - reserved - blocked`

Ensure inventory mutations are transactional and protected against race conditions. Use database transactions and appropriate row locking or atomic updates.

Negative stock policy must be configurable but default to blocking an order reservation that would make available stock negative. Any override requires permission and audit evidence.

## 4.6 Order fulfilment

- Approved orders generate pick lists.
- Warehouse operator sees SKU, quantity, warehouse, and batch/expiry requirements.
- Picking supports full and partial quantities.
- Dispatch creates stock movements and delivery records.
- Delivery status can be updated manually in MVP.
- Returns reference original order/invoice where possible.
- Partial fulfilment must preserve remaining quantities and status.
- Duplicate dispatch actions must not double-decrement inventory.

## 4.7 Financial records and reconciliation

The MVP is an operational reconciliation tool, not a full accounting ledger or statutory accounting system.

Support document/record types:

- Supplier purchase invoice.
- Customer sales invoice.
- Credit note.
- Debit note.
- Customer return.
- Supplier return.
- Payment receipt.
- Payment allocation.
- Scheme claim/benefit record.

Document extraction:

- Upload PDF/image.
- Store original file securely.
- Extract supplier/customer, document number, date, line items, quantities, unit prices, taxes/charges as extracted fields, and totals.
- Validate totals arithmetically.
- Flag unreadable or inconsistent fields.
- Require human verification before a record becomes authoritative.

Matching:

- Exact identifier match where possible.
- Supplier/customer and date windows.
- Amount and currency.
- Line-level SKU/quantity matching where data allows.
- Configurable tolerances.
- Manual match/unmatch with audit trail.

Reconciliation case statuses:

`open`, `investigating`, `awaiting_evidence`, `resolved`, `accepted_difference`, `closed`

Possible discrepancy categories:

- Missing credit note.
- Price mismatch.
- Quantity mismatch.
- Scheme benefit mismatch.
- Duplicate document.
- Unmatched payment.
- Return not credited.
- Unknown difference.

Important distinction: a mismatch is an exception requiring investigation, not proof of fraud, theft, or financial leakage.

Every case must show:
- Records compared.
- Source documents.
- Expected calculation.
- Observed value.
- Difference.
- Matching method.
- Confidence or rule explanation.
- Assigned owner.
- Notes and resolution evidence.
- Full history.

## 4.8 Receivables and payments

- Track invoices and due dates.
- Track outstanding amount.
- Support partial payment allocations.
- Support unallocated payments.
- Calculate ageing buckets using configurable rules.
- Show retailer credit exposure.
- Flag orders that exceed configured credit limits.
- Allow authorized override with reason.
- Never automatically initiate a payment or collection communication.

Use decimal arithmetic and transactional allocation logic. Prevent over-allocation and duplicate allocation.

## 4.9 Exception management

Create a unified work queue for:

- Low-confidence order extraction.
- Unmatched retailer/SKU.
- Insufficient inventory.
- Credit limit exceeded.
- Price mismatch.
- Scheme ambiguity.
- Invoice extraction failure.
- Financial reconciliation mismatch.
- Expiring/expired stock.
- Duplicate document/order.

Each exception has:

- Type and severity.
- Organization and related entity.
- Description.
- Evidence links.
- Suggested next action.
- Assigned user.
- Due date (optional).
- Status.
- Comments.
- Audit history.

AI may recommend an action but must not silently resolve an exception.

## 4.10 Analytics and dashboards

All metrics must be derived from real application records. Demo data may populate the dashboard in demo mode, clearly labeled.

Distributor owner dashboard:

- Orders received/approved/dispatched.
- Gross sales and net sales, with precise definitions.
- Order processing time.
- Order correction rate.
- Inventory value and stock alerts.
- Receivables and ageing.
- Open reconciliation cases.
- Potential discrepancy value, clearly labeled as unverified.
- Resolved discrepancy value, only where resolution is recorded.
- Scheme benefit status.

Operations dashboard:

- Pending orders.
- Orders awaiting review.
- Orders blocked by stock/credit.
- Pick/dispatch queue.
- Returns awaiting processing.
- Exception ageing.

Accountant dashboard:

- Invoices awaiting verification.
- Unmatched records.
- Open reconciliation cases.
- Unallocated payments.
- Credit notes pending.
- Receivables ageing.

Manufacturer dashboard (later phase):

- Only authorized participating distributors.
- Aggregated or scoped sales/stock/scheme metrics.
- Clear data freshness indicators.
- No access to distributor financial or retailer details unless explicitly shared.

Every metric needs a documented formula and test. Avoid ambiguous terms such as “profit” unless cost, discount, tax, returns, and expense treatment are defined. Do not claim net profit when only gross margin data is available.

---

# 5. AI and automation specification

## 5.1 AI design principles

- LLM output is untrusted input.
- Validate all structured outputs.
- Use deterministic services for calculations and business rules.
- Keep source documents and provenance.
- Store model/provider/version and extraction metadata.
- Make low-confidence cases visible.
- Do not train on customer data without explicit lawful basis and agreement.
- Do not send sensitive documents to external AI services unless the organization has configured and authorized that provider.
- Provide a local/demo mode that does not require paid API keys.

## 5.2 Order understanding service

Pipeline:

1. Validate upload type and size.
2. Store file securely.
3. Extract text using OCR or PDF text extraction.
4. Normalize text without destroying source content.
5. Use an LLM provider adapter or deterministic demo parser to produce structured candidates.
6. Validate schema.
7. Match retailer and SKU candidates against tenant master data.
8. Calculate confidence and warnings.
9. Save a draft extraction.
10. Present human review UI.

Do not assume one confidence score is sufficient. Store field-level confidence and match rationale.

If an external LLM key is missing, the application must still work in manual mode and demo mode. Show the provider status accurately.

## 5.3 Scheme document understanding

- Extract candidate rules from uploaded circulars/PDFs.
- Preserve source page/section references where possible.
- Generate a draft structured scheme.
- Run schema validation and business-rule validation.
- Require authorized review and activation.
- Never activate a scheme automatically based only on LLM extraction.

## 5.4 Financial anomaly detection

MVP: deterministic rules and explainable comparisons.

Later: statistical or ML anomaly models only after adequate labeled historical data exists.

Any anomaly model must:
- Have a documented target and training dataset.
- Use time-aware validation where appropriate.
- Report precision/recall and false-positive burden.
- Avoid leakage from future information.
- Expose model version and explanation.
- Be monitored for drift.
- Never make autonomous financial decisions.

## 5.5 AI evaluation

Create a versioned evaluation dataset with ground-truth labels. Include:
- Clear text orders.
- Misspellings and abbreviations.
- Ambiguous product descriptions.
- Multiple units.
- Similar SKUs.
- Poor-quality scans.
- Multi-page invoices.
- Missing fields.
- Scheme circular examples.

Metrics:
- Field-level precision, recall, and exact match.
- SKU top-1/top-3 accuracy.
- Quantity/unit exact match.
- Document extraction error rate.
- Human correction rate.
- False auto-match rate.
- Processing latency and cost per document.

Do not publish accuracy claims until measured on a held-out dataset. Prioritize a low false-match rate over aggressive automation.

---

# 6. Data model

Use PostgreSQL with SQLAlchemy and Alembic. Use UUID primary keys for business entities. Store timestamps in UTC and render in the organization’s configured timezone. Default currency is INR but model currency explicitly.

Suggested core entities:

### Identity and tenancy
- `organizations`
- `users`
- `memberships`
- `roles`
- `permissions`
- `role_permissions`
- `invitations`
- `data_sharing_grants`

### Master data
- `products`
- `product_variants`
- `brands`
- `categories`
- `units_of_measure`
- `unit_conversions`
- `retailers`
- `suppliers`
- `warehouses`
- `price_lists`
- `price_list_items`

### Orders and fulfilment
- `order_sources`
- `orders`
- `order_lines`
- `order_revisions`
- `order_approvals`
- `pick_lists`
- `pick_list_lines`
- `dispatches`
- `deliveries`
- `returns`
- `return_lines`

### Inventory
- `inventory_movements`
- `inventory_reservations`
- `inventory_balances` (optional derived/cache table, never sole source of truth)
- `stock_counts`
- `stock_count_lines`
- `batches`

### Schemes
- `schemes`
- `scheme_versions`
- `scheme_eligibility_rules`
- `scheme_benefit_rules`
- `scheme_applications`
- `scheme_claims`

### Finance
- `documents`
- `document_extractions`
- `purchase_invoices`
- `purchase_invoice_lines`
- `sales_invoices`
- `sales_invoice_lines`
- `credit_notes`
- `debit_notes`
- `payments`
- `payment_allocations`
- `reconciliation_cases`
- `reconciliation_matches`
- `reconciliation_events`

### Platform
- `exceptions`
- `comments`
- `attachments`
- `audit_events`
- `notifications`
- `integration_connections`
- `integration_sync_runs`
- `idempotency_keys`

This is a logical model, not an instruction to create every table before the first workflow. Implement the minimum coherent schema for each phase and evolve it through migrations.

## 6.1 Data integrity requirements

- Use foreign keys.
- Add unique constraints for tenant-scoped business codes.
- Add indexes for common tenant, date, status, SKU, retailer, and document queries.
- Use decimal/numeric database types for money and quantities where fractional quantities are allowed.
- Use explicit currency codes.
- Use transactions for multi-record financial and inventory changes.
- Use optimistic locking/version fields where concurrent edits are possible.
- Use idempotency keys for externally retried create/confirm operations.
- Soft deletion may be used for master data, but financial/inventory records must not be silently deleted.
- Maintain created/updated actor metadata where appropriate.
- Keep an audit trail for sensitive changes.

---

# 7. API design

Use versioned REST APIs under `/api/v1`. Generate OpenAPI documentation from FastAPI.

Suggested route groups:

- `/auth`
- `/organizations`
- `/users`
- `/products`
- `/units`
- `/retailers`
- `/suppliers`
- `/warehouses`
- `/inventory`
- `/price-lists`
- `/schemes`
- `/orders`
- `/order-extractions`
- `/fulfilment`
- `/documents`
- `/purchase-invoices`
- `/sales-invoices`
- `/credit-notes`
- `/payments`
- `/reconciliation`
- `/exceptions`
- `/analytics`
- `/audit`
- `/integrations`

Requirements:

- Consistent request/response schemas.
- Pagination, filtering, and sorting.
- Standard error envelope.
- Input validation.
- Authentication and authorization.
- Tenant scoping.
- Rate limiting for sensitive or expensive endpoints.
- Idempotency on important create/confirm actions.
- OpenAPI examples.
- No leaking stack traces or secrets.

Example error:

```json
{
  "error": {
    "code": "INSUFFICIENT_AVAILABLE_STOCK",
    "message": "The requested quantity exceeds available stock.",
    "details": {
      "sku": "SKU-001",
      "requested": 12,
      "available": 8
    },
    "request_id": "..."
  }
}
```

---

# 8. Frontend and UX specification

## 8.1 Design direction

Build a polished, modern enterprise SaaS interface. It should feel like a serious operational product, not a generic AI landing page.

Visual direction:
- Light, neutral workspace by default.
- Deep navy/charcoal typography.
- Restrained amber accent for important actions and attention states.
- Green for verified/success states.
- Red only for genuine errors or critical exceptions.
- Dense but readable data tables.
- Consistent spacing and typography.
- Clear status chips.
- Strong empty, loading, and error states.
- Responsive layouts for desktop and tablet/mobile.

Do not overuse gradients, glassmorphism, animated backgrounds, oversized cards, or decorative charts.

Accessibility:
- Keyboard navigation.
- Visible focus states.
- Semantic labels.
- Adequate contrast.
- Accessible dialogs and tables.
- Responsive text and controls.

## 8.2 Main navigation

Distributor application:

1. Overview
2. Orders
3. Order Inbox
4. Products
5. Retailers
6. Inventory
7. Schemes
8. Finance
9. Reconciliation
10. Fulfilment
11. Analytics
12. Exceptions
13. Integrations
14. Settings

Show navigation items according to permissions.

## 8.3 Required screens

### Authentication
- Sign in.
- Sign up/invitation acceptance.
- Forgot password/reset.
- Organization onboarding.
- Demo login or demo seed instructions in development only.

### Overview
- KPI summary.
- Order activity.
- Exceptions requiring attention.
- Stock alerts.
- Receivables snapshot.
- Reconciliation summary.
- Date filters and clear metric definitions.

### Order inbox
- List of incoming sources.
- Upload/paste input.
- Processing status.
- Extraction results.
- Confidence indicators.
- Review and correction panel.
- Convert to draft order.

### Orders
- Search/filter.
- Order detail.
- Line items.
- Pricing and scheme breakdown.
- Validation warnings.
- Approval history.
- Status timeline.
- Cancel/amend actions subject to permissions.

### Products and retailers
- Searchable data tables.
- Create/edit forms.
- CSV import with preview and validation.
- Duplicate suggestions.
- Detail pages and activity history.

### Inventory
- Warehouse selector.
- SKU balances.
- Batch/expiry views.
- Stock movement ledger.
- Reservations.
- Adjustments with reason and permission.
- Stock count workflow.

### Schemes
- Scheme list and status.
- Scheme creation/editor.
- Document upload and AI draft extraction.
- Eligibility preview.
- Calculation explanation.
- Version history.
- Approval/activation workflow.

### Finance and reconciliation
- Document upload.
- Extraction review.
- Invoice detail.
- Match suggestions.
- Reconciliation cases.
- Evidence viewer.
- Resolution workflow.
- Payment allocation.

### Exceptions
- Filter by type, severity, owner, status, age.
- Detail panel with evidence.
- Assign, comment, resolve, reopen.
- Resolution reason.

### Analytics
- Sales and order trends.
- Product/retailer performance.
- Inventory movement.
- Receivables ageing.
- Scheme benefits.
- Reconciliation outcomes.
- Export where authorized.

### Settings
- Organization profile.
- Members and roles.
- Warehouses.
- Units and conversion factors.
- Pricing defaults.
- Approval policies.
- Integrations and provider status.
- Audit log.

## 8.4 Interaction rules

- Every visible action must work or be explicitly disabled with an explanation.
- Confirm destructive or consequential actions.
- Show unsaved-change warnings.
- Use optimistic UI only where rollback is safe.
- Show loading, success, and failure states.
- Use server-side pagination for large tables.
- Avoid displaying fabricated data in production mode.
- Mark demo/synthetic data prominently.

---

# 9. Technology architecture

Use a modular monolith with separately executable background workers.

Preferred stack:

### Frontend
- Next.js (current stable version compatible with the environment)
- TypeScript strict mode
- Tailwind CSS
- shadcn/ui or equivalent accessible component system
- React Hook Form and Zod where useful
- TanStack Query or an equivalent server-state solution

### Backend
- Python (supported stable version)
- FastAPI
- Pydantic
- SQLAlchemy
- Alembic
- PostgreSQL

### Async processing
- Celery and Redis, or a simpler queue abstraction if appropriate.
- Jobs for document extraction, imports, exports, analytics refresh, and integration sync.

### Files
- S3-compatible object storage in production.
- Local development storage adapter.
- Private-by-default files.
- Signed, short-lived download URLs where applicable.
- Validate MIME type, extension, file size, and content.
- Malware scanning integration point for production.

### AI
- Provider abstraction for LLMs.
- Claude API adapter when configured.
- OCR/document parser adapter.
- Deterministic local/demo parser for development.
- Versioned JSON schemas and evaluation datasets.

### Testing and delivery
- Pytest.
- Playwright.
- Frontend unit tests using the chosen ecosystem.
- Docker Compose for local dependencies.
- GitHub Actions CI.
- Structured logging and health endpoints.

Do not introduce Kubernetes, Kafka, service mesh, or a separate vector database unless a concrete requirement justifies it. PostgreSQL full-text search and `pgvector` are optional, not mandatory for MVP.

## 9.1 Suggested repository layout

Adapt to existing repository conventions if they are sound.

```text
tradeos/
  apps/
    web/
  services/
    api/
    worker/
  packages/
    shared-contracts/
  intelligence/
    extraction/
    matching/
    evaluation/
  infra/
    docker/
    scripts/
  data/
    synthetic/
    fixtures/
  docs/
    architecture/
    product/
    api/
    security/
    operations/
  tests/
    e2e/
  CLAUDE.md
  SPEC.md
  README.md
  .env.example
  docker-compose.yml
```

A simpler monorepo is acceptable. Avoid creating empty packages solely to match this diagram.

---

# 10. Data strategy and demo environment

## 10.1 Synthetic data

Create a deterministic synthetic-data generator with a fixed seed and configurable volume.

Default demo environment:
- 1 distributor organization.
- 1 manufacturer organization for later permission tests.
- 3 warehouses.
- 100–300 products across several FMCG categories.
- 100–300 retailers.
- 5–10 suppliers/brands.
- 1,000+ orders.
- Purchase and sales invoices.
- Inventory movements.
- Scheme records.
- Payments and credit notes.
- Reconciliation cases.
- Deliberately injected anomalies with ground-truth labels.

The generator must preserve referential integrity and create realistic relationships. Do not create random disconnected records.

Include scenarios:
- Correct order.
- Ambiguous SKU.
- Unknown retailer.
- Duplicate order.
- Insufficient stock.
- Credit limit breach.
- Valid quantity scheme.
- Ineligible scheme.
- Price mismatch.
- Missing credit note.
- Partial payment.
- Return awaiting credit.
- Inventory adjustment with reason.

All demo records must be visibly labeled as synthetic.

## 10.2 CSV import/export

Provide downloadable templates for products, retailers, suppliers, opening stock, and historical transactions.

Import process:
1. Upload.
2. Parse.
3. Map columns.
4. Preview.
5. Validate.
6. Show row-level errors.
7. Confirm import.
8. Record import batch and audit event.

Never partially import silently. Define transaction/batch semantics and provide a clear report.

## 10.3 Customer data

Real customer data is not available by default. Do not scrape, invent, or imply access to private distributor records.

Support data onboarding through customer-authorized CSV/document imports. Provide data retention and deletion controls appropriate to the product and legal review.

---

# 11. Integrations

Implement an adapter architecture.

Potential integrations:
- Tally.
- Busy.
- Marg ERP.
- WhatsApp Business Platform.
- Email/document ingestion.
- Payment/banking data imports.

MVP integration priority:
1. CSV import/export.
2. Generic documented API/webhook interface.
3. One accounting integration only if credentials, documentation, and a test environment are available.

Each integration must have:
- Connection status.
- Configuration validation.
- Credential storage outside source code.
- Sync logs.
- Retry handling.
- Idempotency.
- Error queue.
- Last successful sync.
- Manual reconciliation path.

Never claim an integration is live unless a real authenticated round trip has been tested.

WhatsApp: Do not use unofficial scraping or personal-account automation. Use an authorized business API integration when configured. Otherwise provide copy/paste and upload workflows.

---

# 12. Security, privacy, and reliability

Implement security from the beginning.

## 12.1 Authentication
- Secure password hashing using a modern algorithm.
- Short-lived access tokens or secure server sessions.
- Refresh/session rotation as appropriate.
- Password reset with expiring, single-use tokens.
- Rate limiting on authentication.
- Optional MFA extension point.
- Secure cookies where cookie sessions are used.

## 12.2 Authorization
- Server-side RBAC.
- Tenant isolation.
- Object-level authorization.
- Manufacturer data-sharing scopes.
- Audit sensitive access and mutations.

## 12.3 Data protection
- TLS in deployment.
- Encryption at rest through infrastructure/storage.
- Secrets via environment/secret manager.
- No secrets in logs.
- Private document storage.
- Signed URLs.
- File validation.
- Configurable retention and deletion processes.

## 12.4 Audit
Audit:
- Login/security events.
- Role and permission changes.
- Product/price/scheme changes.
- Order approvals and amendments.
- Inventory adjustments and dispatch.
- Invoice and payment changes.
- Reconciliation decisions.
- Data exports.
- Integration changes.
- Data-sharing grants.

Audit records should capture actor, tenant, action, entity, timestamp, request/correlation ID, and appropriate before/after data. Avoid storing secrets or unnecessary sensitive document contents in logs.

## 12.5 Reliability
- Health/readiness endpoints.
- Structured logs.
- Error tracking integration point.
- Database backups and documented restore process.
- Retry with bounded backoff.
- Dead-letter/error handling for failed jobs.
- Idempotent job processing.
- Graceful handling of unavailable AI providers.
- Database migration checks in CI.

Do not advertise an uptime SLA until production infrastructure and monitoring support it.

---

# 13. Testing and acceptance criteria

## 13.1 Required test layers

### Unit tests
- Pricing.
- Unit conversion.
- Scheme eligibility and benefit calculations.
- Credit exposure.
- Payment allocation.
- Inventory availability.
- Reconciliation matching.
- Permission evaluation.
- AI output schema validation.

### Integration tests
- API/database.
- Tenant isolation.
- Order-to-inventory workflow.
- Invoice extraction persistence.
- Reconciliation case creation and resolution.
- Background jobs.
- File upload validation.

### End-to-end tests
1. Create organization and owner.
2. Add product and retailer.
3. Add opening stock.
4. Create manual order.
5. Validate and approve order.
6. Reserve and dispatch stock.
7. Upload supplier invoice.
8. Review extracted fields.
9. Create and resolve reconciliation case.
10. Record payment and allocate it to invoice.
11. Verify dashboard metrics.
12. Verify unauthorized user is blocked.

## 13.2 Critical invariants

Tests must prove:

- A tenant cannot read or mutate another tenant’s records.
- A confirmed order cannot be duplicated by retrying the same request.
- An order cannot reserve more than available stock unless an explicit authorized override policy permits it.
- Dispatch cannot double-decrement stock.
- Inventory balances reconcile to the movement ledger.
- Payment allocations cannot exceed available payment or invoice balances.
- Financial calculations use decimal arithmetic and documented rounding.
- A scheme cannot be applied outside its effective period or eligibility rules.
- An ambiguous AI match cannot silently become a confirmed order.
- A user without permission cannot approve an order or change a scheme.
- Financial and inventory mutations create audit events.
- Dashboard totals reconcile with underlying transactions.

## 13.3 Quality gates

Before declaring a phase complete:
- Format/lint checks pass.
- Type checks pass.
- Relevant unit and integration tests pass.
- Migrations apply on a clean database.
- E2E tests for affected critical flows pass.
- No unresolved critical security issue is known.
- No broken primary navigation or inert primary action.
- Documentation updated.

Do not hide flaky tests or reduce test coverage merely to obtain a green build. Report flaky or environment-blocked tests honestly.

---

# 14. Implementation roadmap and autonomous execution

Execute the phases in order. Continue without routine user confirmation.

## Phase 0 — Repository inspection and project setup
- Inspect repository and runtime.
- Record existing stack and code.
- Preserve useful work.
- Create/update `CLAUDE.md`, `README.md`, `.env.example`, and `docs/BUILD_PROGRESS.md`.
- Establish local development and CI.
- Create architecture decision records for meaningful choices.

Exit: reproducible development environment and documented architecture.

## Phase 1 — Foundation
- Authentication.
- Organization and membership.
- RBAC.
- Tenant context and isolation.
- Audit events.
- Base UI shell and navigation.
- Database migrations.

Exit: multiple organizations and roles work with isolation tests.

## Phase 2 — Master data
- Products, variants, units and conversions.
- Retailers and suppliers.
- Warehouses.
- CSV import/export.
- Search, filters, forms, validation.

Exit: complete master-data lifecycle.

## Phase 3 — Inventory and orders
- Inventory movement ledger.
- Opening balances.
- Manual order entry.
- Pricing.
- Validation.
- Reservations.
- Approval and order status.
- Basic fulfilment/dispatch.

Exit: manual order-to-dispatch flow with correct inventory.

## Phase 4 — AI order inbox
- File/text ingestion.
- OCR/PDF extraction.
- LLM provider adapter.
- Structured extraction schema.
- SKU/retailer candidate matching.
- Confidence and review interface.
- Draft order creation.
- Evaluation harness and sample fixtures.

Exit: complete source-to-reviewed-draft workflow. Manual order workflow remains usable if AI provider is unavailable.

## Phase 5 — Schemes
- Scheme model and versioning.
- Rule engine.
- Eligibility and benefit calculations.
- Scheme extraction as draft.
- Approval and audit.
- Order calculation integration.

Exit: deterministic tested scheme calculations and reviewable extracted drafts.

## Phase 6 — Finance and reconciliation
- Document management.
- Purchase invoices and lines.
- Credit/debit notes.
- Payments and allocations.
- Record matching.
- Reconciliation cases and evidence.
- Exception workflow.

Exit: end-to-end reconciliation with explainable differences and audit history.

## Phase 7 — Analytics and operational polish
- Role-specific dashboards.
- Metric definitions.
- Exports.
- Exception analytics.
- Search and usability.
- Empty/loading/error states.
- Responsive layouts.

Exit: dashboards reconcile to source records and core journeys are usable.

## Phase 8 — Hardening and pilot readiness
- Security review.
- Tenant isolation review.
- Performance testing.
- Backup/restore documentation.
- E2E suite.
- Accessibility pass.
- Deployment configuration.
- Demo seed and onboarding guide.
- Known limitations register.

Exit: pilot-ready candidate, not an assertion of commercial validation.

## Phase 9 — Optional integrations and manufacturer portal
Only after distributor core is stable:
- Real accounting connector where credentials and test environment exist.
- Authorized WhatsApp Business integration.
- Manufacturer organization workflow.
- Explicit distributor data-sharing grants.
- Manufacturer dashboards with scoped access.

Do not allow optional integrations to block completion of the distributor product.

---

# 15. Definition of done for the whole project

The project is ready for a controlled pilot when all of the following are true:

- [ ] Local setup works from a clean checkout.
- [ ] Environment variables are documented.
- [ ] Demo data can be seeded deterministically.
- [ ] Authentication works.
- [ ] Roles and tenant isolation are tested.
- [ ] Product and retailer management works.
- [ ] CSV import reports row-level errors.
- [ ] Inventory ledger and balances are correct.
- [ ] Manual orders work end-to-end.
- [ ] Order extraction supports configured provider and fallback/manual mode.
- [ ] Ambiguous extraction requires human review.
- [ ] Pricing and schemes are deterministic and tested.
- [ ] Purchase documents can be uploaded and verified.
- [ ] Reconciliation cases can be created, investigated, and resolved.
- [ ] Payment allocation and receivables work.
- [ ] Exceptions can be assigned and resolved.
- [ ] Dashboards use real database queries.
- [ ] Audit events exist for sensitive operations.
- [ ] Critical E2E tests pass.
- [ ] Security and operational limitations are documented.
- [ ] No feature is falsely presented as an active external integration.
- [ ] Pilot onboarding and user guide exist.

---

# 16. Required documentation

Maintain these files as implementation progresses:

- `README.md` — setup, commands, architecture overview, demo access.
- `CLAUDE.md` — persistent engineering rules and project context.
- `SPEC.md` — this master specification; update only when requirements materially change.
- `docs/BUILD_PROGRESS.md` — phase status, tests, blockers, next action.
- `docs/ARCHITECTURE.md` — actual implemented architecture.
- `docs/DATA_MODEL.md` — schema and relationships.
- `docs/API.md` — API conventions and important endpoints.
- `docs/SECURITY.md` — threat assumptions, controls, operational guidance.
- `docs/AI_EVALUATION.md` — datasets, metrics, measured results, limitations.
- `docs/INTEGRATIONS.md` — real vs simulated connectors and setup.
- `docs/USER_GUIDE.md` — workflows for distributor users.
- `docs/DECISIONS/` — architecture decision records.

Documentation must describe the implementation as it exists, not merely the intended design.

---

# 17. Final instruction to the engineering agent

Begin now.

1. Inspect the repository and report its current state in `docs/BUILD_PROGRESS.md`.
2. Identify any conflicts between existing code and this specification.
3. Preserve working code and propose the smallest safe path to the target architecture.
4. Create or update the project instruction files.
5. Implement Phase 0 and then continue through the phases autonomously.
6. Run the application and tests regularly, not only at the end.
7. Prioritize a complete, reliable distributor workflow over superficial breadth.
8. If time or context limits interrupt execution, leave the repository in a working state and update `docs/BUILD_PROGRESS.md` with the exact next task and commands.
9. Never claim that a phase or the project is complete unless its exit criteria are verified.
10. At the end of each work session, provide a concise report of completed work, tests, known issues, and the next concrete task.

**Build a real operational product, not a collection of screens.**
