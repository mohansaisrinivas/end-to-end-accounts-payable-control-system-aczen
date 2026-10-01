import os
import json
from forensic_engine import DeepInvoiceForensics

def run():
    real_invoice_path = "Cream and White Professional Business Invoice.pdf"
    fake_invoice_path = "GENUINE TEMPLATES\1.pdf"

    if not os.path.exists(real_invoice_path) or not os.path.exists(fake_invoice_path):
        print("[ERROR] Ensure both 'sample_invoice.pdf' and 'fake_invoice.pdf' exist in the directory.")
        return

    engine = DeepInvoiceForensics()

    # Step 1: Extract baseline spatial patch grid from golden template
    print(f"\n[DB SIMULATION] Extracting Spatial Patch Grid from '{real_invoice_path}'...")
    golden_image = engine._load_document(real_invoice_path)
    golden_patch_tensor = engine._extract_normalized_patch_embeddings(golden_image)

    # Step 2: Validate Golden Template against itself
    print(f"\n================ TEST 1: '{real_invoice_path}' (EXPECTED: AUTHENTIC) ================")
    result_clean = engine.analyze_invoice(real_invoice_path, baseline_patches=golden_patch_tensor)
    print(json.dumps(result_clean, indent=2))

    # Step 3: Validate Uploaded File against the Patch Grid
    print(f"\n=============== TEST 2: '{fake_invoice_path}' (EXPECTED: FLAGGED) ===============")
    result_spoof = engine.analyze_invoice(fake_invoice_path, baseline_patches=golden_patch_tensor)
    print(json.dumps(result_spoof, indent=2))

if __name__ == "__main__":
    run()