from datetime import datetime
from typing import Optional, List
from sqlmodel import Field, SQLModel, create_engine, Session, select, Relationship

# ==========================================
# 1. MASTER DATA TABLES
# ==========================================

class VendorMaster(SQLModel, table=True):
    __tablename__ = "vendor_master"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    tax_id: str = Field(index=True)  # GSTIN or EIN
    verified_bank_account: str
    payment_terms: str = Field(default="Net 30")
    
    # NEW: Stores the serialized DiT spatial patch embedding or global vector for visual matching
    golden_layout_embedding: Optional[str] = Field(default=None)

    # Relationships
    purchase_orders: List["PurchaseOrders"] = Relationship(back_populates="vendor")
    invoices: List["Invoices"] = Relationship(back_populates="vendor")


class PurchaseOrders(SQLModel, table=True):
    __tablename__ = "purchase_orders"

    id: Optional[int] = Field(default=None, primary_key=True)
    vendor_id: int = Field(foreign_key="vendor_master.id")
    po_number: str = Field(unique=True, index=True)
    status: str = Field(default="OPEN")  # OPEN, MATCHED, CLOSED
    total_authorized: float

    # Relationships
    vendor: Optional[VendorMaster] = Relationship(back_populates="purchase_orders")
    line_items: List["PoLineItems"] = Relationship(back_populates="purchase_order")
    goods_receipts: List["GoodsReceipts"] = Relationship(back_populates="purchase_order")


class PoLineItems(SQLModel, table=True):
    __tablename__ = "po_line_items"

    id: Optional[int] = Field(default=None, primary_key=True)
    po_id: int = Field(foreign_key="purchase_orders.id")
    sku: str = Field(index=True)
    description: str
    authorized_qty: float
    authorized_price: float

    # Relationships
    purchase_order: Optional[PurchaseOrders] = Relationship(back_populates="line_items")


class GoodsReceipts(SQLModel, table=True):
    __tablename__ = "goods_receipts"

    id: Optional[int] = Field(default=None, primary_key=True)
    grn_number: str = Field(unique=True, index=True)
    po_id: int = Field(foreign_key="purchase_orders.id")
    date_received: str  # Stored as YYYY-MM-DD string

    # Relationships
    purchase_order: Optional[PurchaseOrders] = Relationship(back_populates="goods_receipts")
    grn_line_items: List["GrnLineItems"] = Relationship(back_populates="goods_receipt")


class GrnLineItems(SQLModel, table=True):
    __tablename__ = "grn_line_items"

    id: Optional[int] = Field(default=None, primary_key=True)
    grn_id: int = Field(foreign_key="goods_receipts.id")
    po_line_id: int = Field(foreign_key="po_line_items.id")
    sku: str
    qty_received: float

    # Relationships
    goods_receipt: Optional[GoodsReceipts] = Relationship(back_populates="grn_line_items")


# ==========================================
# 2. TRANSACTIONAL AP TABLES
# ==========================================

class Invoices(SQLModel, table=True):
    __tablename__ = "invoices"

    id: Optional[int] = Field(default=None, primary_key=True)
    vendor_id: int = Field(foreign_key="vendor_master.id")
    invoice_number: str = Field(index=True)
    s3_file_uri: str
    tamper_score: float
    total_billed: float
    status: str = Field(default="PENDING_REVIEW")  # PENDING_REVIEW, APPROVED, EXCEPTION_HOLD

    # Relationships
    vendor: Optional[VendorMaster] = Relationship(back_populates="invoices")
    line_items: List["InvoiceLineItems"] = Relationship(back_populates="invoice")
    audit_logs: List["AuditLog"] = Relationship(back_populates="invoice")


class InvoiceLineItems(SQLModel, table=True):
    __tablename__ = "invoice_line_items"

    id: Optional[int] = Field(default=None, primary_key=True)
    invoice_id: int = Field(foreign_key="invoices.id")
    sku: str
    billed_qty: float
    billed_price: float
    confidence_score: float

    # Relationships
    invoice: Optional[Invoices] = Relationship(back_populates="line_items")


class AuditLog(SQLModel, table=True):
    __tablename__ = "audit_log"

    id: Optional[int] = Field(default=None, primary_key=True)
    invoice_id: int = Field(foreign_key="invoices.id")
    action: str  # e.g., "INITIAL_INGESTION", "MANUAL_OVERRIDE"
    user_id: str  # System or employee ID
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    previous_state: str
    new_state: str

    # Relationships
    invoice: Optional[Invoices] = Relationship(back_populates="audit_logs")


# ==========================================
# 3. DB ENGINE & INITIALIZATION
# ==========================================

sqlite_file_name = "invoice_system.db"
engine = create_engine(f"sqlite:///{sqlite_file_name}", echo=False)

def create_db_and_tables():
    """Generates all tables based on the full relational schema."""
    SQLModel.metadata.create_all(engine)

if __name__ == "__main__":
    print("[INIT] Creating database tables from schema...")
    create_db_and_tables()
    print("[SUCCESS] Database tables created successfully with Vendor Embedding support!")