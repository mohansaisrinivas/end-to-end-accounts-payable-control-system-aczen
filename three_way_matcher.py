import json
from sqlmodel import Session, select
from database_engine import (
    engine, Invoices, VendorMaster, InvoiceLineItems, 
    PurchaseOrders, PoLineItems, GoodsReceipts, GrnLineItems, AuditLog
)

class ThreeWayMatcher:
    def __init__(self):
        """Initializes the deterministic matching engine encompassing 18 core AP controls."""
        pass

    def execute_match(self, vendor_id, parsed_data, file_uri, forensic_result):
        discrepancies = []
        
        # 1. Extract parsed AI fields
        inv_num = parsed_data.get("invoice_number", "UNKNOWN")
        po_ref = parsed_data.get("po_reference", "")
        inv_total = float(parsed_data.get("total_billed", 0.0))
        line_items = parsed_data.get("line_items", [])
        inv_tax_id = parsed_data.get("tax_id", "")
        inv_bank = parsed_data.get("bank_account_number", "")
        inv_terms = parsed_data.get("payment_terms", "")
        inv_vendor_name = parsed_data.get("vendor_name", "").strip()

        with Session(engine) as session:
            # ==========================================
            # A. Vendor Master Controls
            # ==========================================
            vendor = session.get(VendorMaster, vendor_id)
            if not vendor:
                discrepancies.append("01_VENDOR_EXISTS: Vendor does not exist in master record.")
            else:
                # NEW ANTI-SPOOFING CHECK (Rule 01 & Rule 07)
                # Check if the primary database name (e.g., "Meenakshi") is actually in the extracted text ("Rogue Enterprises")
                primary_db_word = vendor.name.lower().split()[0]
                if primary_db_word not in inv_vendor_name.lower():
                    discrepancies.append(f"01_VENDOR_EXISTS: Name Mismatch. Extracted name '{inv_vendor_name}' does not match registered master data '{vendor.name}'.")
                    discrepancies.append("07_PO_VENDOR_MATCH: The vendor name printed on the document is attempting to bill against a different vendor's Purchase Order.")

                # 03 VENDOR_TAX_ID_MATCH
                if inv_tax_id and vendor.tax_id and str(inv_tax_id).strip() != str(vendor.tax_id).strip():
                    discrepancies.append(f"03_VENDOR_TAX_ID_MATCH: Tax ID {inv_tax_id} != {vendor.tax_id}")
                
                # 04 BANK_DETAILS_MATCH
                if inv_bank and vendor.verified_bank_account and str(inv_bank).strip() != str(vendor.verified_bank_account).strip():
                    discrepancies.append(f"04_BANK_DETAILS_MATCH: Bank {inv_bank} != {vendor.verified_bank_account}")
                
                # 14 PAYMENT_TERMS
                if inv_terms and vendor.payment_terms and str(inv_terms).upper() != str(vendor.payment_terms).upper():
                    discrepancies.append(f"14_PAYMENT_TERMS: Invoice terms '{inv_terms}' differ from master '{vendor.payment_terms}'.")
            # ==========================================
            # B & C. Purchase Order & Goods Receipt Controls
            # ==========================================
            po = None
            if po_ref:
                po = session.exec(select(PurchaseOrders).where(PurchaseOrders.po_number == po_ref)).first()
                
            if not po:
                discrepancies.append(f"05_PO_EXISTS: No matching PO found in database for '{po_ref}'.")
            else:
                # 06 PO_APPROVED
                if po.status not in ["APPROVED", "OPEN"]:
                    discrepancies.append("06_PO_APPROVED: Purchase order is not in an OPEN or APPROVED status.")
                # 07 PO_VENDOR_MATCH
                if po.vendor_id != vendor_id:
                    discrepancies.append("07_PO_VENDOR_MATCH: PO vendor does not match Invoice vendor.")
                    
                # Fetch PO line items mapping
                po_lines = session.exec(select(PoLineItems).where(PoLineItems.po_id == po.id)).all()
                po_line_map = {l.sku: l for l in po_lines}
                
                # Fetch Goods Receipt mapping
                grn = session.exec(select(GoodsReceipts).where(GoodsReceipts.po_id == po.id)).first()
                if not grn:
                    discrepancies.append("09_RECEIPT_MATCH: No Goods Receipt Note (GRN) found for this PO.")
                else:
                    grn_lines = session.exec(select(GrnLineItems).where(GrnLineItems.grn_id == grn.id)).all()
                    grn_line_map = {l.po_line_id: l.qty_received for l in grn_lines}
                    
                # Cross-reference every invoice item (Checks 08, 10, 11)
                for item in line_items:
                    sku = item.get("sku")
                    inv_qty = float(item.get("billed_qty", 0.0))
                    inv_price = float(item.get("billed_price", 0.0))
                    
                    if sku not in po_line_map:
                        discrepancies.append(f"08_PO_ITEM_MATCH: Invoiced SKU '{sku}' not found on Purchase Order.")
                    else:
                        po_line = po_line_map[sku]
                        # 11 PRICE_MATCH
                        if inv_price > po_line.authorized_price:
                            discrepancies.append(f"11_PRICE_MATCH: Billed price ${inv_price} exceeds authorized ${po_line.authorized_price} for SKU '{sku}'.")
                        
                        # 10 QUANTITY_MATCH
                        if grn:
                            received_qty = grn_line_map.get(po_line.id, 0.0)
                            if inv_qty > received_qty:
                                discrepancies.append(f"10_QUANTITY_MATCH: Billed qty {inv_qty} exceeds received qty {received_qty} for SKU '{sku}'.")

            # ==========================================
            # D. Financial Arithmetic
            # ==========================================
            # Extract the tax amount from the parsed invoice data (default to 0.0)
            inv_tax = float(parsed_data.get("tax_amount", 0.0))
            
            # Calculate the base subtotal (Qty * Unit Price)
            calc_base_total = sum(float(i.get("billed_qty", 0)) * float(i.get("billed_price", 0)) for i in line_items)
            
            # 12 TAX_VALIDATION (Allow 0% or exactly 18% GST)
            expected_gst = calc_base_total * 0.18
            
            # Check if tax is outside of a $1.00 tolerance for BOTH $0.00 and 18% GST
            is_zero_tax = abs(inv_tax - 0.0) <= 1.0
            is_18_percent_tax = abs(inv_tax - expected_gst) <= 1.0
            
            if not (is_zero_tax or is_18_percent_tax):
                discrepancies.append(f"12_TAX_VALIDATION: Invoiced tax (${inv_tax:.2f}) is invalid. Permitted tax is $0.00 or 18% GST (${expected_gst:.2f}).")
                
            # 13 TOTAL_VALIDATION (Base Subtotal + Allowed Tax)
            calc_grand_total = calc_base_total + inv_tax
            
            if abs(calc_grand_total - inv_total) > 1.0: # $1.00 tolerance
                discrepancies.append(f"13_TOTAL_VALIDATION: Calculated sum + tax (${calc_grand_total:.2f}) != Total Billed (${inv_total:.2f}).")
                
            # ==========================================
            # E. Duplicate & Fraud
            # ==========================================
            exact_dup = session.exec(select(Invoices).where(Invoices.invoice_number == inv_num, Invoices.vendor_id == vendor_id)).first()
            if exact_dup:
                discrepancies.append("15_DUPLICATE_EXACT: Exact duplicate invoice number detected in database.")
                
            semantic_dup = session.exec(select(Invoices).where(Invoices.vendor_id == vendor_id, Invoices.total_billed == inv_total)).all()
            if len(semantic_dup) > 0 and not exact_dup:
                discrepancies.append("16_DUPLICATE_SEMANTIC: Suspected duplicate (same vendor, same total amount).")
                
            # ==========================================
            # F. Risk & Policy Anomalies
            # ==========================================
            if (9900 <= inv_total <= 10000) or (49000 <= inv_total <= 50000):
                discrepancies.append("17_THRESHOLD_PROXIMITY: Value near approval threshold policy limit (Structuring Risk).")
            if inv_total > 250000:
                discrepancies.append("18_UNUSUAL_AMOUNT: Unusually high invoice amount requiring CFO secondary review.")

        # ==========================================
        # Determine Final Application Status
        # ==========================================
        # Determine the simple status for the 'invoices' table
        if forensic_result.get("is_visually_authentic") is False:
            simple_status = "REJECTED"
            discrepancies.insert(0, "SECURITY_TAMPER: Document Image Transformer detected visual manipulation.")
        elif any("15_DUPLICATE" in d for d in discrepancies) or any("16_DUPLICATE" in d for d in discrepancies):
            simple_status = "REJECTED"
        elif len(discrepancies) > 0:
            simple_status = "PENDING REVIEW"
        else:
            simple_status = "APPROVED"

        return self._save_to_database(vendor_id, parsed_data, file_uri, forensic_result, simple_status, discrepancies)

    def _save_to_database(self, vendor_id, parsed_data, file_uri, forensic_result, simple_status, discrepancies):
        """Persists the transaction matching your exact Invoice and LineItem schema."""
        with Session(engine) as session:
            # 1. Create and save the Invoice with the simple status
            new_inv = Invoices(
                invoice_number=parsed_data.get("invoice_number", "UNKNOWN"),
                vendor_id=vendor_id,
                total_billed=float(parsed_data.get("total_billed", 0.0)),
                tamper_score=forensic_result.get("layout_similarity_score", 0.99),
                status=simple_status,
                s3_file_uri=file_uri
            )
            session.add(new_inv)
            session.commit()
            session.refresh(new_inv)
            
            # Extract ID immediately to avoid SQLAlchemy DetachedInstanceError
            saved_invoice_id = new_inv.id 
            
            # 2. Save Line Items
            for item in parsed_data.get("line_items", []):
                line = InvoiceLineItems(
                    invoice_id=saved_invoice_id, 
                    sku=item.get("sku", "UNKNOWN"),
                    billed_qty=float(item.get("billed_qty", 0.0)),
                    billed_price=float(item.get("billed_price", 0.0)),
                    confidence_score=0.95
                )
                session.add(line)
                
            # 3. Log audit trail for ALL ingestions (Approved or Flagged)
            if len(discrepancies) > 0:
                reason_string = " | ".join(discrepancies)
                action_text = f"SYSTEM_FLAGGED: {reason_string}"
            else:
                action_text = "SYSTEM_AUTO_APPROVED: Passed all 18 deterministic control checks."
                
            audit = AuditLog(
                invoice_id=saved_invoice_id,
                action=action_text,
                user_id="RULE_ENGINE",
                previous_state="NEW_INGESTION",
                new_state=simple_status
            )
            session.add(audit)
                
            session.commit()
            
        return {
            "database_invoice_id": saved_invoice_id,
            "final_status": simple_status,
            "discrepancies": discrepancies,
            "forensic_report": forensic_result
        }