import os
from typing import List, Optional
from pydantic import BaseModel, Field
from google import genai
from dotenv import load_dotenv

# Load environment variables from a .env file if present
load_dotenv()

# ==========================================
# 1. STRUCTURED SCHEMAS (Matches DB & Team 3 Contract)
# ==========================================

class ParsedLineItem(BaseModel):
    sku: str = Field(description="Product SKU, part number, or item code")
    description: str = Field(description="Detailed item description")
    billed_qty: float = Field(description="Quantity billed on the invoice")
    billed_price: float = Field(description="Unit price for the item")

class ParsedInvoiceSchema(BaseModel):
    vendor_name: str = Field(description="Legal name of the vendor issuing the invoice")
    tax_id: str = Field(description="Vendor Tax ID, GSTIN, or EIN")
    bank_account_number: str = Field(description="Bank account number listed for payment remittance")
    invoice_number: str = Field(description="Unique invoice number identifier")
    po_reference: Optional[str] = Field(default=None, description="Purchase Order reference number if present")
    grn_reference: Optional[str] = Field(default=None, description="Goods Receipt Note reference if present")
    total_billed: float = Field(description="Grand total final amount billed")
    line_items: List[ParsedLineItem] = Field(description="Array of all billed line items")


# ==========================================
# 2. AI PARSER CLASS
# ==========================================

class AIInvoiceParser:
    def __init__(self):
        print("[INIT] Initializing Google GenAI Client for Document Extraction...")
        
        # Explicitly pull the API key from environment variables
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY environment variable not found! Please check your .env file or environment settings.")

        # Initialize the GenAI client with the explicit API key
        self.client = genai.Client(api_key=api_key)

    def extract_invoice_data(self, file_path: str) -> dict:
        """
        Uploads the invoice PDF/image to Gemini and extracts structured JSON 
        enforcing the Pydantic schema.
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Invoice file not found: {file_path}")

        print(f"[PARSER] Uploading '{file_path}' to Gemini API...")
        uploaded_file = self.client.files.upload(file=file_path)

        prompt = (
            "Analyze this invoice document thoroughly. Extract the vendor name, tax ID (GSTIN/EIN), "
            "remittance bank account number, invoice number, PO reference, GRN reference, grand total, "
            "and all line items with their quantities and prices."
        )

        print("[PARSER] Extracting structured fields using Gemini model...")
        response = self.client.models.generate_content(
            model='gemini-3.1-flash-lite',  # Updated to the active flash model
            contents=[uploaded_file, prompt],
            config={
                'response_mime_type': 'application/json',
                'response_schema': ParsedInvoiceSchema,
            },
        )

        # Validate and convert response into a standard Python dictionary
        parsed_result = ParsedInvoiceSchema.model_validate_json(response.text)
        return parsed_result.model_dump()


if __name__ == "__main__":
    # Quick test if sample invoice is present
    target_pdf = "Cream and White Professional Business Invoice (1).pdf"
    if os.path.exists(target_pdf):
        parser = AIInvoiceParser()
        data = parser.extract_invoice_data(target_pdf)
        import json
        print("\n[PARSER OUTPUT]:")
        print(json.dumps(data, indent=2))
    else:
        print(f"[INFO] Place '{target_pdf}' in the directory to test the parser.")