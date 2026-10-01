import os
import json
import ctypes
from ctypes import wintypes
import torch
from sqlmodel import Session, select
from database_engine import engine, VendorMaster
from forensic_engine import DeepInvoiceForensics
from invoice_parser import AIInvoiceParser
from three_way_matcher import ThreeWayMatcher

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

def main():
    print("==================================================")
    print("  AI INVOICE FORENSIC & RECONCILIATION GATEWAY CLI")
    print("==================================================")

    # 1. Select the incoming invoice file
    print("\n[STEP 1] Please select the INCOMING INVOICE file to verify...")
    test_path = browse_file_windows("Select Incoming Invoice to Verify")
    
    if not test_path:
        print("[EXIT] No file selected. Operation cancelled.")
        return
    print(f"-> Loaded Target File: {os.path.basename(test_path)}")

    # 2. Extract Data (Happens immediately for ALL files)
    print("\n[STEP 2] Parsing document data to identify vendor and line items...")
    parser_engine = AIInvoiceParser()
    forensic_engine = DeepInvoiceForensics()

    try:
        parsed_data = parser_engine.extract_invoice_data(test_path)
        vendor_name = parsed_data.get("vendor_name")
        print(f"-> Identified Vendor: {vendor_name}")
    except Exception as e:
        print(f"[ERROR] AI Parsing failed: {e}")
        return

    # 3. Query Database for Vendor's Stored Golden Layout Embedding
    print("\n[STEP 3] Fetching Vendor Golden Template from Database...")
    golden_patch_tensor = None
    vendor_id = 1  # Default fallback if DB empty during testing
    
    with Session(engine) as session:
        statement = select(VendorMaster).where(VendorMaster.name.ilike(f"%{vendor_name}%"))
        vendor_record = session.exec(statement).first()

        if vendor_record:
            vendor_id = vendor_record.id
            if vendor_record.golden_layout_embedding:
                print(f"[DB] Found registered vendor ID {vendor_id} with stored layout embedding.")
                embedding_list = json.loads(vendor_record.golden_layout_embedding)
                golden_patch_tensor = torch.tensor(embedding_list)
            else:
                print("[WARNING] Vendor found, but no golden template embedding stored.")
        else:
            print("[WARNING] Vendor not found in DB! Using default system checks.")

    # 4. Run Visual Forensics Gate
    print("\n[STEP 4] Running Visual Tampering & Micro-Geometry Check...")
    forensic_result = forensic_engine.analyze_invoice(test_path, baseline_patches=golden_patch_tensor)
    
    print("\n================ FORENSIC REPORT ================")
    print(json.dumps(forensic_result, indent=2))
    print("=================================================")

    # 5. Database Logging & 3-Way Match Execution
    print("\n[STEP 5] Logging to Database and Executing Financial Rules...")
    matcher = ThreeWayMatcher()
    
    # The matcher handles the logic gate automatically based on the forensic_result
    match_results = matcher.execute_match(
        vendor_id=vendor_id,
        parsed_data=parsed_data,
        file_uri=test_path,
        forensic_result=forensic_result
    )

    print("\n=============== PIPELINE EXECUTION RESULTS ===============")
    print(f"Final DB Status : {match_results['final_status']}")
    
    if match_results['discrepancies']:
        print("Discrepancies   :")
        for d in match_results['discrepancies']:
            print(f"  - {d}")
    else:
        print("Discrepancies   : None. Perfect Match!")
        
    print(f"Saved to DB as  : Invoice ID #{match_results['database_invoice_id']}")
    print("=====================================================")

if __name__ == "__main__":
    main()