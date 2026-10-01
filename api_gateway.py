import os
import json
import shutil
import torch
from fastapi import FastAPI, HTTPException, UploadFile, File, Request, Form
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select
from sqlalchemy import func, or_

# Included PurchaseOrders in imports
from database_engine import engine, Invoices, VendorMaster, AuditLog, InvoiceLineItems, PurchaseOrders
from invoice_parser import AIInvoiceParser
from forensic_engine import DeepInvoiceForensics
from three_way_matcher import ThreeWayMatcher

app = FastAPI(title="Accounts Payable Forensic MPA")
templates = Jinja2Templates(directory="templates")

UPLOAD_DIR = "uploaded_invoices"
os.makedirs(UPLOAD_DIR, exist_ok=True)

parser_engine = AIInvoiceParser()
forensic_engine = DeepInvoiceForensics()
matcher_engine = ThreeWayMatcher()

# ==========================================
# 1. HTML WEB ROUTES
# ==========================================

@app.get("/", response_class=HTMLResponse)
def dashboard_view(request: Request):
    """Renders the main dashboard HTML page with Graph Data."""
    with Session(engine) as session:
        # KPI Counts
        total = session.exec(select(func.count(Invoices.id))).one()
        approved = session.exec(select(func.count(Invoices.id)).where(Invoices.status == "APPROVED")).one()
        rejected = session.exec(select(func.count(Invoices.id)).where(Invoices.status == "REJECTED")).one()
        pending = session.exec(select(func.count(Invoices.id)).where(Invoices.status == "PENDING REVIEW")).one()
        
        # Broadened Exception Breakdown Buckets using or_()
        price_err = session.exec(select(func.count(AuditLog.id)).where(or_(
            AuditLog.action.like("%PRICE_MATCH%"), 
            AuditLog.action.like("%TOTAL_VALIDATION%"), 
            AuditLog.action.like("%TAX_VALIDATION%")
        ))).one()
        
        qty_err = session.exec(select(func.count(AuditLog.id)).where(AuditLog.action.like("%QUANTITY_MATCH%"))).one()
        
        grn_err = session.exec(select(func.count(AuditLog.id)).where(AuditLog.action.like("%RECEIPT_MATCH%"))).one()
        
        vendor_err = session.exec(select(func.count(AuditLog.id)).where(or_(
            AuditLog.action.like("%VENDOR_EXISTS%"), 
            AuditLog.action.like("%PO_VENDOR_MATCH%"), 
            AuditLog.action.like("%BANK_DETAILS_MATCH%")
        ))).one()
        
        stp_rate = round((approved / total * 100), 1) if total > 0 else 0

        return templates.TemplateResponse(
            request=request,
            name="dashboard.html", 
            context={
                "total": total, "approved": approved, "rejected": rejected, "pending": pending,
                "price_err": price_err, "qty_err": qty_err, "grn_err": grn_err, "vendor_err": vendor_err,
                "stp_rate": stp_rate, "current_tab": "dashboard"
            }
        )

@app.get("/invoices/{tab}", response_class=HTMLResponse)
def invoice_list_view(request: Request, tab: str):
    """Dynamic route for All, Pending, Approved, and Rejected queues."""
    with Session(engine) as session:
        statement = select(Invoices, VendorMaster.name).join(VendorMaster)
        
        # Filters using the EXACT new status strings
        if tab == "pending":
            statement = statement.where(Invoices.status == "PENDING REVIEW")
        elif tab == "approved":
            statement = statement.where(Invoices.status == "APPROVED")
        elif tab == "rejected":
            statement = statement.where(Invoices.status == "REJECTED")

        results = session.exec(statement).all()
        
        invoices = []
        for inv, vendor_name in results:
            lines = session.exec(select(InvoiceLineItems).where(InvoiceLineItems.invoice_id == inv.id)).all()
            line_data = [{"sku": l.sku, "qty": l.billed_qty, "price": l.billed_price, "total": l.billed_qty * l.billed_price} for l in lines]
            
            # Fetch detailed reason from AuditLog to show in the UI Warning Banner
            audit = session.exec(select(AuditLog).where(AuditLog.invoice_id == inv.id).order_by(AuditLog.id.desc())).first()
            detailed_reason = audit.action if audit else inv.status

            invoices.append({
                "id": inv.id,
                "invoice_number": inv.invoice_number,
                "vendor_name": vendor_name,
                "total_billed": inv.total_billed,
                "tamper_score": round(inv.tamper_score, 2),
                "status": inv.status,  # Used for the small color pill
                "detailed_reason": detailed_reason,  # Used for the big warning text
                "line_items": line_data
            })

        return templates.TemplateResponse(
            request=request,
            name="invoices.html", 
            context={"invoices": invoices, "current_tab": tab}
        )

# ==========================================
# 2. FORM SUBMISSION ROUTES
# ==========================================

@app.post("/action/review/{invoice_id}")
def handle_manual_review(invoice_id: int, action: str = Form(...), reason: str = Form(...)):
    """Handles the HTML form submission for approving/rejecting invoices."""
    with Session(engine) as session:
        invoice = session.get(Invoices, invoice_id)
        if not invoice:
            raise HTTPException(status_code=404, detail="Invoice not found")

        previous_state = invoice.status
        new_state = "APPROVED" if action == "APPROVE" else "REJECTED"

        invoice.status = new_state
        session.add(invoice)

        audit = AuditLog(
            invoice_id=invoice.id, 
            action=f"MANUAL_{action}: {reason}", 
            user_id="FINANCE_ADMIN_01",
            previous_state=previous_state, 
            new_state=new_state
        )
        session.add(audit)
        session.commit()

    return RedirectResponse(url="/invoices/pending", status_code=303)


@app.post("/action/upload")
def handle_file_upload(file: UploadFile = File(...)):
    """Handles file uploads directly from the HTML form."""
    file_path = os.path.join(UPLOAD_DIR, file.filename)
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    parsed_data = parser_engine.extract_invoice_data(file_path)
    vendor_name = parsed_data.get("vendor_name", "")
    po_ref = parsed_data.get("po_reference", "")

    golden_patch_tensor = None
    vendor_id = 1
    
    with Session(engine) as session:
        vendor_record = session.exec(select(VendorMaster).where(VendorMaster.name.ilike(f"%{vendor_name}%"))).first()
        
        if not vendor_record and po_ref:
            po_record = session.exec(select(PurchaseOrders).where(PurchaseOrders.po_number == po_ref)).first()
            if po_record:
                vendor_record = session.get(VendorMaster, po_record.vendor_id)
                
        if vendor_record:
            vendor_id = vendor_record.id
            if vendor_record.golden_layout_embedding:
                golden_patch_tensor = torch.tensor(json.loads(vendor_record.golden_layout_embedding))

    forensic_result = forensic_engine.analyze_invoice(file_path, baseline_patches=golden_patch_tensor)
    matcher_engine.execute_match(vendor_id, parsed_data, file_path, forensic_result)

    return RedirectResponse(url="/", status_code=303)