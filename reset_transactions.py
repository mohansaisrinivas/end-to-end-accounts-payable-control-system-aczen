from sqlmodel import Session, delete
from database_engine import engine, Invoices, InvoiceLineItems, AuditLog

def reset_transactional_data():
    print("==================================================")
    print("  TRANSACTIONAL DATA RESET UTILITY")
    print("==================================================")
    
    confirm = input("WARNING: This will delete all processed Invoices, Line Items, and Audit Logs. \nMaster data (Vendors, POs, GRNs) will be kept safe. \nContinue? (y/n): ").strip().lower()
    
    if confirm != 'y':
        print("[ABORTED] No data was deleted.")
        return

    print("\n[INIT] Connecting to database...")
    
    with Session(engine) as session:
        try:
            # 1. Delete child tables first to respect foreign key constraints
            print("-> Wiping Audit Logs...")
            session.exec(delete(AuditLog))
            
            print("-> Wiping Invoice Line Items...")
            session.exec(delete(InvoiceLineItems))
            
            # 2. Delete parent table
            print("-> Wiping Invoices...")
            session.exec(delete(Invoices))
            
            # 3. Commit changes to the database
            session.commit()
            
            print("\n[SUCCESS] All transactional records have been successfully truncated!")
            print("[INFO] Your 'Always True' master data remains completely intact.")
            
        except Exception as e:
            session.rollback()
            print(f"\n[ERROR] An error occurred during deletion: {e}")

if __name__ == "__main__":
    reset_transactional_data()