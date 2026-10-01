import os
import json
import ctypes
from ctypes import wintypes
from sqlmodel import Session, SQLModel, select
from database_engine import (
    engine, 
    VendorMaster, 
    PurchaseOrders, 
    PoLineItems, 
    GoodsReceipts, 
    GrnLineItems
)
from forensic_engine import DeepInvoiceForensics

def browse_file_windows(title_message: str) -> str:
    """Opens the native Windows OpenFile dialog box using ctypes."""
    class OPENFILENAMEW(ctypes.Structure):
        _fields_ = [
            ("lStructSize", wintypes.DWORD), ("hwndOwner", wintypes.HWND), ("hInstance", wintypes.HINSTANCE),
            ("lpstrFilter", wintypes.LPCWSTR), ("lpstrCustomFilter", wintypes.LPWSTR), ("nMaxCustFilter", wintypes.DWORD),
            ("nFilterIndex", wintypes.DWORD), ("lpstrFile", wintypes.LPWSTR), ("nMaxFile", wintypes.DWORD),
            ("lpstrFileTitle", wintypes.LPWSTR), ("nMaxFileTitle", wintypes.DWORD), ("lpstrInitialDir", wintypes.LPCWSTR),
            ("lpstrTitle", wintypes.LPCWSTR), ("Flags", wintypes.DWORD), ("nFileOffset", wintypes.WORD),
            ("nFileExtension", wintypes.WORD), ("lpstrDefExt", wintypes.LPCWSTR), ("lCustData", wintypes.LPARAM),
            ("lpfnHook", ctypes.c_void_p), ("lpTemplateName", wintypes.LPCWSTR), ("pvReserved", ctypes.c_void_p),
            ("dwReserved", wintypes.DWORD), ("FlagsEx", wintypes.DWORD),
        ]

    ofn = OPENFILENAMEW()
    ofn.lStructSize = ctypes.sizeof(ofn)
    ofn.hwndOwner = None
    ofn.lpstrFilter = "Document Files (*.pdf;*.png;*.jpg;*.jpeg)\0*.pdf;*.png;*.jpg;*.jpeg\0All Files (*.*)\0*.*\0"
    buffer = ctypes.create_unicode_buffer(260)
    ofn.lpstrFile = ctypes.cast(buffer, wintypes.LPWSTR)
    ofn.nMaxFile = 260
    ofn.lpstrTitle = title_message
    ofn.Flags = 0x00080000 | 0x00001000 | 0x00000004

    if ctypes.windll.comdlg32.GetOpenFileNameW(ctypes.byref(ofn)):
        return ofn.lpstrFile
    return ""

def truncate_database():
    """Wipes all transactional and master data from the database."""
    print("\n[WARNING] This action will WIPE ALL DATA (Vendors, POs, Invoices, Audit Logs).")
    confirm = input("Type 'YES' to confirm truncation: ").strip()
    if confirm == 'YES':
        print("[RESET] Truncating and rebuilding database schema...")
        SQLModel.metadata.drop_all(engine)
        SQLModel.metadata.create_all(engine)
        print("[SUCCESS] Database successfully truncated and rebuilt!")
    else:
        print("[ABORTED] Truncation cancelled.")

def add_master_data(session: Session, forensic_engine: DeepInvoiceForensics):
    """Flow for adding 'Always True' Master Data."""
    print("\n--------------------------------------------------")
    print("  ADD NEW MASTER DATA (VENDOR -> PO -> GRN)")
    print("--------------------------------------------------")
    
    v_name = input("Vendor Name: ").strip()
    v_tax = input("Tax ID (GSTIN/EIN): ").strip()
    v_bank = input("Verified Bank Account Number: ").strip()
    v_terms = input("Payment Terms (e.g., Net 30 Days): ").strip()

    print(f"\n[ACTION] Please select the GENUINE GOLDEN TEMPLATE file for {v_name}...")
    golden_file = browse_file_windows(f"Select Genuine Template for {v_name}")
    
    embedding_str = None
    if golden_file and os.path.exists(golden_file):
        print(f"-> Processing template: '{os.path.basename(golden_file)}'...")
        img = forensic_engine._load_document(golden_file)
        patch_tensor = forensic_engine._extract_normalized_patch_embeddings(img)
        embedding_str = json.dumps(patch_tensor.tolist())
        print("-> Embedding successfully generated!")
    else:
        print("[WARNING] No file selected. Vendor will be saved WITHOUT a visual embedding.")

    # Save Vendor
    vendor = VendorMaster(
        name=v_name, tax_id=v_tax, verified_bank_account=v_bank, 
        payment_terms=v_terms, golden_layout_embedding=embedding_str
    )
    session.add(vendor)
    session.commit()
    session.refresh(vendor)

    print("\n--- PURCHASE ORDER DETAILS ---")
    po_num = input("PO Number: ").strip()
    po_amt = float(input("Total Authorized Amount: ").strip())
    
    po = PurchaseOrders(vendor_id=vendor.id, po_number=po_num, status="OPEN", total_authorized=po_amt)
    session.add(po)
    session.commit()
    session.refresh(po)

    print("\n--- PO LINE ITEM DETAILS ---")
    po_items_list = []
    
    while True:
        sku = input("SKU/Item Code: ").strip()
        desc = input("Item Description: ").strip()
        qty = float(input("Authorized Quantity: ").strip())
        price = float(input("Authorized Unit Price: ").strip())

        po_item = PoLineItems(po_id=po.id, sku=sku, description=desc, authorized_qty=qty, authorized_price=price)
        session.add(po_item)
        session.commit()
        session.refresh(po_item)
        po_items_list.append(po_item)
        
        print(f"-> Added {sku} to Purchase Order {po_num}")
        if input("\nAdd another line item to this PO? (y/n): ").strip().lower() != 'y':
            break

    print("\n--- GOODS RECEIPT NOTE (GRN) ---")
    grn_num = input("GRN Number: ").strip()
    grn_date = input("Date Received (YYYY-MM-DD): ").strip()

    grn = GoodsReceipts(grn_number=grn_num, po_id=po.id, date_received=grn_date)
    session.add(grn)
    session.commit()
    session.refresh(grn)

    print(f"-> Auto-generating GRN receipts for all {len(po_items_list)} PO line items...")
    for item in po_items_list:
        grn_item = GrnLineItems(grn_id=grn.id, po_line_id=item.id, sku=item.sku, qty_received=item.authorized_qty)
        session.add(grn_item)
    session.commit()

    print(f"\n[SUCCESS] Master Data logged successfully for '{v_name}'!")

def update_master_data(session: Session):
    """Update existing 'Always True' data fields."""
    print("\n--- UPDATE MASTER DATA ---")
    print("1. Update Vendor Record")
    print("2. Update Purchase Order")
    choice = input("Select entity to update: ").strip()

    if choice == '1':
        vendors = session.exec(select(VendorMaster)).all()
        print("\nAvailable Vendors:")
        for v in vendors:
            print(f" ID: {v.id} | Name: {v.name}")
        
        try:
            v_id = int(input("\nEnter Vendor ID to update: "))
            vendor = session.get(VendorMaster, v_id)
            if not vendor:
                print("[ERROR] Vendor not found.")
                return

            print(f"\nUpdating Vendor: {vendor.name} (Press Enter to keep current value)")
            name = input(f"Name [{vendor.name}]: ").strip()
            tax = input(f"Tax ID [{vendor.tax_id}]: ").strip()
            bank = input(f"Bank Account [{vendor.verified_bank_account}]: ").strip()
            terms = input(f"Terms [{vendor.payment_terms}]: ").strip()

            if name: vendor.name = name
            if tax: vendor.tax_id = tax
            if bank: vendor.verified_bank_account = bank
            if terms: vendor.payment_terms = terms
            
            session.add(vendor)
            session.commit()
            print("[SUCCESS] Vendor details updated!")
        except ValueError:
            print("[ERROR] Invalid ID.")

    elif choice == '2':
        pos = session.exec(select(PurchaseOrders)).all()
        print("\nAvailable Purchase Orders:")
        for p in pos:
            print(f" ID: {p.id} | PO Number: {p.po_number} | Status: {p.status}")
        
        try:
            po_id = int(input("\nEnter PO ID to update: "))
            po = session.get(PurchaseOrders, po_id)
            if not po:
                print("[ERROR] PO not found.")
                return

            print(f"\nUpdating PO: {po.po_number} (Press Enter to keep current value)")
            status = input(f"Status [{po.status}]: ").strip()
            total = input(f"Total Authorized [{po.total_authorized}]: ").strip()

            if status: po.status = status
            if total: po.total_authorized = float(total)
            
            session.add(po)
            session.commit()
            print("[SUCCESS] PO details updated!")
        except ValueError:
            print("[ERROR] Invalid input.")

def delete_master_data(session: Session):
    """Delete an 'Always True' record from the database."""
    print("\n--- DELETE MASTER DATA ---")
    print("1. Delete Vendor")
    print("2. Delete Purchase Order")
    choice = input("Select entity to delete: ").strip()

    if choice == '1':
        vendors = session.exec(select(VendorMaster)).all()
        print("\nAvailable Vendors:")
        for v in vendors:
            print(f" ID: {v.id} | Name: {v.name}")
        try:
            v_id = int(input("\nEnter Vendor ID to DELETE: "))
            vendor = session.get(VendorMaster, v_id)
            if vendor:
                session.delete(vendor)
                session.commit()
                print(f"[SUCCESS] Vendor '{vendor.name}' deleted.")
            else:
                print("[ERROR] Vendor not found.")
        except Exception as e:
            print(f"[ERROR] Could not delete vendor: {e}")

    elif choice == '2':
        pos = session.exec(select(PurchaseOrders)).all()
        print("\nAvailable Purchase Orders:")
        for p in pos:
            print(f" ID: {p.id} | PO Number: {p.po_number}")
        try:
            po_id = int(input("\nEnter PO ID to DELETE: "))
            po = session.get(PurchaseOrders, po_id)
            if po:
                session.delete(po)
                session.commit()
                print(f"[SUCCESS] PO '{po.po_number}' deleted.")
            else:
                print("[ERROR] PO not found.")
        except Exception as e:
            print(f"[ERROR] Could not delete PO: {e}")

def interactive_manager():
    """Main CLI Loop for the Database Administrator."""
    print("==================================================")
    print("  DATABASE ADMINISTRATOR MANAGER")
    print("==================================================")
    
    forensic_engine = None  # Lazy-loaded only if adding a vendor

    with Session(engine) as session:
        while True:
            print("\n" + "="*45)
            print(" 1. Add Master Data (Vendor/PO/GRN)")
            print(" 2. Update Master Data")
            print(" 3. Delete Master Data")
            print(" 4. Truncate Entire Database")
            print(" 5. Exit")
            print("="*45)
            
            choice = input("Select an operation (1-5): ").strip()
            
            if choice == '1':
                if not forensic_engine:
                    print("\n[INIT] Loading Deep Learning Engine for Layout Embeddings...")
                    forensic_engine = DeepInvoiceForensics()
                add_master_data(session, forensic_engine)
            elif choice == '2':
                update_master_data(session)
            elif choice == '3':
                delete_master_data(session)
            elif choice == '4':
                truncate_database()
            elif choice == '5':
                print("[EXIT] Closing Database Manager.")
                break
            else:
                print("[ERROR] Invalid choice. Please select 1-5.")

if __name__ == "__main__":
    interactive_manager()