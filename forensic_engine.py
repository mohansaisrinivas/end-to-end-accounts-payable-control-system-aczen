import os
import io
import pymupdf
import torch
import torch.nn.functional as F
import numpy as np
from PIL import Image, ImageChops
from transformers import AutoImageProcessor, AutoModel

class DeepInvoiceForensics:
    def __init__(self):
        print("[INIT] Loading Document Image Transformer (microsoft/dit-base)...")
        self.layout_processor = AutoImageProcessor.from_pretrained("microsoft/dit-base")
        self.layout_model = AutoModel.from_pretrained("microsoft/dit-base")
        self.layout_model.eval()
        print("[INIT] Document Vision Model initialized successfully.")

    def _load_document(self, file_path: str) -> Image.Image:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Target document not found: {file_path}")

        _, ext = os.path.splitext(file_path.lower())

        if ext == ".pdf":
            doc = pymupdf.open(file_path)
            if len(doc) == 0:
                raise ValueError("The provided PDF file contains no pages.")
            first_page = doc[0]
            pixmap = first_page.get_pixmap(dpi=150)
            image = Image.frombytes("RGB", [pixmap.width, pixmap.height], pixmap.samples)
            doc.close()
            return image
        else:
            return Image.open(file_path).convert("RGB")

    def _check_pixel_tampering(self, image: Image.Image) -> float:
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=90)
        buffer.seek(0)
        compressed_image = Image.open(buffer)

        ela_diff = ImageChops.difference(image, compressed_image)
        ela_array = np.array(ela_diff, dtype=np.float32)

        variance = np.std(ela_array)
        normalized_score = min(float(variance / 15.0), 1.0)
        return round(normalized_score, 4)

    def _extract_normalized_patch_embeddings(self, image: Image.Image) -> torch.Tensor:
        """
        Extracts all 196 spatial patch tokens, removes dominant coordinate spikes
        via Layer Normalization, and L2-normalizes each feature vector.
        Output shape: [196, 768]
        """
        inputs = self.layout_processor(images=image, return_tensors="pt")
        with torch.no_grad():
            outputs = self.layout_model(**inputs)

        # Skip token 0 (CLS) and take tokens 1..196 (the 14x14 spatial patches)
        patches = outputs.last_hidden_state[:, 1:, :]  # [1, 196, 768]

        # 1. Zero-center and scale across feature dimensions to eliminate anisotropy
        mean = patches.mean(dim=-1, keepdim=True)
        std = patches.std(dim=-1, keepdim=True) + 1e-6
        norm_patches = (patches - mean) / std

        # 2. L2 normalize across embedding dimension
        norm_patches = F.normalize(norm_patches, p=2, dim=-1).squeeze(0)  # [196, 768]
        return norm_patches

    def _get_layout_embedding(self, image: Image.Image) -> list:
        """
        Generates the 768-dimensional golden vector representation for DB storage.
        """
        patches = self._extract_normalized_patch_embeddings(image)
        # Global spatial mean of normalized patches
        global_vector = patches.mean(dim=0)
        global_vector = F.normalize(global_vector, p=2, dim=-1)
        return global_vector.cpu().numpy().tolist()

    def analyze_invoice(self, file_path: str, baseline_patches: torch.Tensor = None) -> dict:
        """
        Evaluates pixel-level tampering and structural micro-geometry across the spatial grid.
        """
        try:
            image = self._load_document(file_path)
            pixel_score = self._check_pixel_tampering(image)
            current_patches = self._extract_normalized_patch_embeddings(image)

            # Global preview vector for API contract consistency
            global_preview = current_patches.mean(dim=0).cpu().numpy().tolist()

            if baseline_patches is not None:
                # Calculate cosine similarity for each individual spatial patch across the document
                # baseline_patches: [196, 768], current_patches: [196, 768]
                patch_similarities = (baseline_patches * current_patches).sum(dim=-1)  # [196]

                # Micro-geometry sensitivity:
                # 1. Macro structural score (mean across all tiles)
                mean_similarity = patch_similarities.mean().item()
                # 2. Worst modified tile (catches localized footer/table tampering)
                min_similarity = patch_similarities.min().item()

                # Weighted score: 70% overall layout + 30% localized micro-integrity
                layout_similarity_score = (0.7 * mean_similarity) + (0.3 * max(min_similarity, 0.0))
            else:
                layout_similarity_score = 1.0

            # Security thresholds
            tamper_flagged = pixel_score > 0.15
            layout_flagged = layout_similarity_score < 0.75

            flags = []
            if tamper_flagged:
                flags.append("HIGH_RISK_PIXEL_ALTERATION")
            if layout_flagged:
                flags.append("UNKNOWN_TEMPLATE_FOR_VENDOR")

            return {
                "is_visually_authentic": not (tamper_flagged or layout_flagged),
                "pixel_tampering_score": pixel_score,
                "layout_similarity_score": round(layout_similarity_score, 4),
                "flags": flags,
                "_raw_layout_embedding_preview": global_preview[:5]
            }

        except Exception as e:
            return {
                "is_visually_authentic": False,
                "error": str(e),
                "flags": ["FILE_CORRUPTION_OR_PROCESSING_ERROR"]
            }