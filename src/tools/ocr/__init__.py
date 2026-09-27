"""OCR and Text Extraction Tools

This module provides optical character recognition capabilities using
pytesseract and opencv for extracting text from images and screenshots.
"""

from .ocr_manager import (
    extract_text_from_image,
    extract_text_from_screenshot,
    extract_text_from_window,
    extract_text_from_region,
    find_text_in_image,
    get_text_confidence,
    preprocess_image_for_ocr,
    detect_text_regions
)

__all__ = [
    'extract_text_from_image',
    'extract_text_from_screenshot',
    'extract_text_from_window',
    'extract_text_from_region',
    'find_text_in_image',
    'get_text_confidence',
    'preprocess_image_for_ocr',
    'detect_text_regions'
]
