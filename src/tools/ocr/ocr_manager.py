"""OCR and Text Extraction Tools using pytesseract and opencv

Provides comprehensive text extraction capabilities including:
- Text extraction from images and screenshots
- Window and region-specific text extraction
- Text confidence scoring
- Image preprocessing for better OCR results
- Text region detection
"""

import pytesseract
import cv2
import numpy as np
from PIL import Image
import os
from typing import Optional, List, Dict, Tuple
import time

# Import screenshot tools
try:
    from ..screenshot.screenshot_manager import screenshot_manager
except ImportError:
    # Fallback if screenshot tools not available
    screenshot_manager = None


class OCRManager:
    """Manager class for OCR operations"""
    
    def __init__(self):
        self.tesseract_path = self._find_tesseract_path()
        if self.tesseract_path:
            pytesseract.pytesseract.tesseract_cmd = self.tesseract_path
    
    def _find_tesseract_path(self) -> Optional[str]:
        """Find Tesseract executable path"""
        possible_paths = [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
            r"C:\Users\{}\AppData\Local\Tesseract-OCR\tesseract.exe".format(os.getenv('USERNAME', '')),
            "tesseract"  # If it's in PATH
        ]
        
        for path in possible_paths:
            if os.path.isfile(path):
                return path
        
        # Try to find it in PATH
        try:
            import shutil
            if shutil.which("tesseract"):
                return "tesseract"
        except:
            pass
        
        return None
    
    def preprocess_image_for_ocr(self, image: Image.Image, method: str = "default") -> Image.Image:
        """Preprocess image for better OCR results
        
        Args:
            image: PIL Image object
            method: Preprocessing method ("default", "enhanced", "console", "unity")
            
        Returns:
            Preprocessed PIL Image
            
        Example:
            processed_img = preprocess_image_for_ocr(img, "enhanced")
        """
        try:
            # Convert PIL to OpenCV format
            img_array = np.array(image)
            
            if method == "default":
                # Basic preprocessing
                gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
                processed = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
                
            elif method == "enhanced":
                # Enhanced preprocessing for better text recognition
                gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
                
                # Noise reduction
                denoised = cv2.medianBlur(gray, 3)
                
                # Contrast enhancement
                clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
                enhanced = clahe.apply(denoised)
                
                # Binarization
                processed = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
                
            elif method == "console":
                # Optimized for console/terminal text
                gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
                
                # Invert colors for dark console backgrounds
                inverted = cv2.bitwise_not(gray)
                
                # Morphological operations to clean up
                kernel = np.ones((1,1), np.uint8)
                processed = cv2.morphologyEx(inverted, cv2.MORPH_CLOSE, kernel)
                
            elif method == "unity":
                # Optimized for Unity console errors
                gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
                
                # Adaptive thresholding for varying backgrounds
                processed = cv2.adaptiveThreshold(
                    gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2
                )
                
            else:
                # Default method
                gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
                processed = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
            
            # Convert back to PIL Image
            return Image.fromarray(processed)
            
        except Exception as e:
            print(f"Warning: Image preprocessing failed: {e}")
            return image  # Return original image if preprocessing fails
    
    def extract_text_from_image(self, image_path: str, language: str = "eng", 
                              preprocessing: str = "default") -> Dict:
        """Extract text from an image file
        
        Args:
            image_path: Path to the image file
            language: Tesseract language code (e.g., "eng", "eng+fra")
            preprocessing: Image preprocessing method
            
        Returns:
            Dictionary with extracted text and confidence
            
        Example:
            result = extract_text_from_image("screenshot.png")
            print(result['text'])
        """
        try:
            # Load image
            image = Image.open(image_path)
            
            # Preprocess if requested
            if preprocessing != "none":
                image = self.preprocess_image_for_ocr(image, preprocessing)
            
            # Extract text with confidence scores
            data = pytesseract.image_to_data(image, lang=language, output_type=pytesseract.Output.DICT)
            
            # Combine text and calculate average confidence
            text_parts = []
            confidences = []
            
            for i in range(len(data['text'])):
                text = data['text'][i].strip()
                conf = int(data['conf'][i])
                
                if text and conf > 0:  # Skip empty text and zero confidence
                    text_parts.append(text)
                    confidences.append(conf)
            
            extracted_text = ' '.join(text_parts)
            avg_confidence = sum(confidences) / len(confidences) if confidences else 0
            
            return {
                'text': extracted_text,
                'confidence': avg_confidence,
                'word_count': len(text_parts),
                'raw_data': data
            }
            
        except Exception as e:
            return {
                'text': '',
                'confidence': 0,
                'error': f"Failed to extract text: {e}"
            }
    
    def extract_text_from_screenshot(self, monitor: int = 1, language: str = "eng",
                                   preprocessing: str = "default") -> Dict:
        """Extract text from a screenshot
        
        Args:
            monitor: Monitor number to capture
            language: Tesseract language code
            preprocessing: Image preprocessing method
            
        Returns:
            Dictionary with extracted text and confidence
            
        Example:
            result = extract_text_from_screenshot()
            print(result['text'])
        """
        try:
            if not screenshot_manager:
                return {'text': '', 'confidence': 0, 'error': 'Screenshot tools not available'}
            
            # Take screenshot
            screenshot_path = screenshot_manager.take_screenshot(monitor)
            if screenshot_path.startswith("❌"):
                return {'text': '', 'confidence': 0, 'error': screenshot_path}
            
            # Extract text from screenshot
            result = self.extract_text_from_image(screenshot_path, language, preprocessing)
            
            # Clean up temporary screenshot file
            try:
                os.remove(screenshot_path)
            except:
                pass
            
            return result
            
        except Exception as e:
            return {'text': '', 'confidence': 0, 'error': f"Failed to extract text from screenshot: {e}"}
    
    def extract_text_from_window(self, window_title: str, language: str = "eng",
                               preprocessing: str = "default") -> Dict:
        """Extract text from a specific window
        
        Args:
            window_title: Title of the window to capture
            language: Tesseract language code
            preprocessing: Image preprocessing method
            
        Returns:
            Dictionary with extracted text and confidence
            
        Example:
            result = extract_text_from_window("Unity")
            print(result['text'])
        """
        try:
            if not screenshot_manager:
                return {'text': '', 'confidence': 0, 'error': 'Screenshot tools not available'}
            
            # Take window screenshot
            screenshot_path = screenshot_manager.take_window_screenshot(window_title)
            if screenshot_path.startswith("❌"):
                return {'text': '', 'confidence': 0, 'error': screenshot_path}
            
            # Extract text from screenshot
            result = self.extract_text_from_image(screenshot_path, language, preprocessing)
            
            # Clean up temporary screenshot file
            try:
                os.remove(screenshot_path)
            except:
                pass
            
            return result
            
        except Exception as e:
            return {'text': '', 'confidence': 0, 'error': f"Failed to extract text from window: {e}"}
    
    def extract_text_from_region(self, left: int, top: int, width: int, height: int,
                               language: str = "eng", preprocessing: str = "default") -> Dict:
        """Extract text from a specific screen region
        
        Args:
            left: Left coordinate of region
            top: Top coordinate of region
            width: Width of region
            height: Height of region
            language: Tesseract language code
            preprocessing: Image preprocessing method
            
        Returns:
            Dictionary with extracted text and confidence
            
        Example:
            result = extract_text_from_region(100, 100, 800, 600)
            print(result['text'])
        """
        try:
            if not screenshot_manager:
                return {'text': '', 'confidence': 0, 'error': 'Screenshot tools not available'}
            
            # Take region screenshot
            screenshot_path = screenshot_manager.take_region_screenshot(left, top, width, height)
            if screenshot_path.startswith("❌"):
                return {'text': '', 'confidence': 0, 'error': screenshot_path}
            
            # Extract text from screenshot
            result = self.extract_text_from_image(screenshot_path, language, preprocessing)
            
            # Clean up temporary screenshot file
            try:
                os.remove(screenshot_path)
            except:
                pass
            
            return result
            
        except Exception as e:
            return {'text': '', 'confidence': 0, 'error': f"Failed to extract text from region: {e}"}
    
    def find_text_in_image(self, image_path: str, search_text: str, 
                          language: str = "eng") -> List[Dict]:
        """Find specific text in an image and return bounding boxes
        
        Args:
            image_path: Path to the image file
            search_text: Text to search for
            language: Tesseract language code
            
        Returns:
            List of dictionaries with text locations and confidence
            
        Example:
            results = find_text_in_image("screenshot.png", "error")
            for result in results:
                print(f"Found '{result['text']}' at {result['bbox']}")
        """
        try:
            # Load and preprocess image
            image = Image.open(image_path)
            processed_image = self.preprocess_image_for_ocr(image, "enhanced")
            
            # Get detailed text data
            data = pytesseract.image_to_data(processed_image, lang=language, 
                                           output_type=pytesseract.Output.DICT)
            
            results = []
            search_lower = search_text.lower()
            
            for i in range(len(data['text'])):
                text = data['text'][i].strip()
                conf = int(data['conf'][i])
                
                if text and conf > 30 and search_lower in text.lower():
                    results.append({
                        'text': text,
                        'confidence': conf,
                        'bbox': {
                            'left': data['left'][i],
                            'top': data['top'][i],
                            'width': data['width'][i],
                            'height': data['height'][i]
                        }
                    })
            
            return results
            
        except Exception as e:
            return [{'error': f"Failed to find text: {e}"}]
    
    def get_text_confidence(self, image_path: str, language: str = "eng") -> float:
        """Get average confidence score for text extraction
        
        Args:
            image_path: Path to the image file
            language: Tesseract language code
            
        Returns:
            Average confidence score (0-100)
            
        Example:
            confidence = get_text_confidence("screenshot.png")
            print(f"Confidence: {confidence}%")
        """
        try:
            result = self.extract_text_from_image(image_path, language)
            return result.get('confidence', 0)
            
        except Exception as e:
            return 0.0
    
    def detect_text_regions(self, image_path: str, min_confidence: int = 30) -> List[Dict]:
        """Detect regions in image that contain text
        
        Args:
            image_path: Path to the image file
            min_confidence: Minimum confidence threshold for text detection
            
        Returns:
            List of text regions with bounding boxes
            
        Example:
            regions = detect_text_regions("screenshot.png")
            print(f"Found {len(regions)} text regions")
        """
        try:
            # Load and preprocess image
            image = Image.open(image_path)
            processed_image = self.preprocess_image_for_ocr(image, "enhanced")
            
            # Get detailed text data
            data = pytesseract.image_to_data(processed_image, 
                                           output_type=pytesseract.Output.DICT)
            
            regions = []
            
            for i in range(len(data['text'])):
                text = data['text'][i].strip()
                conf = int(data['conf'][i])
                
                if text and conf >= min_confidence:
                    regions.append({
                        'text': text,
                        'confidence': conf,
                        'bbox': {
                            'left': data['left'][i],
                            'top': data['top'][i],
                            'width': data['width'][i],
                            'height': data['height'][i]
                        },
                        'level': data['level'][i],
                        'page_num': data['page_num'][i],
                        'block_num': data['block_num'][i],
                        'par_num': data['par_num'][i],
                        'line_num': data['line_num'][i],
                        'word_num': data['word_num'][i]
                    })
            
            return regions
            
        except Exception as e:
            return [{'error': f"Failed to detect text regions: {e}"}]


# Global instance
ocr_manager = OCRManager()


# Convenience functions
def extract_text_from_image(image_path: str, language: str = "eng", 
                          preprocessing: str = "default") -> Dict:
    """Extract text from an image file"""
    return ocr_manager.extract_text_from_image(image_path, language, preprocessing)


def extract_text_from_screenshot(monitor: int = 1, language: str = "eng",
                               preprocessing: str = "default") -> Dict:
    """Extract text from a screenshot"""
    return ocr_manager.extract_text_from_screenshot(monitor, language, preprocessing)


def extract_text_from_window(window_title: str, language: str = "eng",
                           preprocessing: str = "default") -> Dict:
    """Extract text from a specific window"""
    return ocr_manager.extract_text_from_window(window_title, language, preprocessing)


def extract_text_from_region(left: int, top: int, width: int, height: int,
                           language: str = "eng", preprocessing: str = "default") -> Dict:
    """Extract text from a specific screen region"""
    return ocr_manager.extract_text_from_region(left, top, width, height, language, preprocessing)


def find_text_in_image(image_path: str, search_text: str, 
                      language: str = "eng") -> List[Dict]:
    """Find specific text in an image"""
    return ocr_manager.find_text_in_image(image_path, search_text, language)


def get_text_confidence(image_path: str, language: str = "eng") -> float:
    """Get average confidence score for text extraction"""
    return ocr_manager.get_text_confidence(image_path, language)


def preprocess_image_for_ocr(image: Image.Image, method: str = "default") -> Image.Image:
    """Preprocess image for better OCR results"""
    return ocr_manager.preprocess_image_for_ocr(image, method)


def detect_text_regions(image_path: str, min_confidence: int = 30) -> List[Dict]:
    """Detect regions in image that contain text"""
    return ocr_manager.detect_text_regions(image_path, min_confidence)


# Example usage and test functions
if __name__ == "__main__":
    # Test the OCR tools
    print("=== OCR Tools Test ===")
    
    print("\n1. Tesseract setup:")
    if ocr_manager.tesseract_path:
        print(f"  ✅ Tesseract found at: {ocr_manager.tesseract_path}")
    else:
        print("  ❌ Tesseract not found. Please install Tesseract OCR.")
    
    print("\n2. Available test commands:")
    print("  - extract_text_from_screenshot() - Extract text from current screen")
    print("  - extract_text_from_window('Unity') - Extract text from Unity window")
    print("  - extract_text_from_region(100, 100, 800, 600) - Extract text from region")
    print("  - find_text_in_image('image.png', 'error') - Find specific text")
    
    print("\n3. Preprocessing methods:")
    print("  - 'default' - Basic preprocessing")
    print("  - 'enhanced' - Enhanced for better recognition")
    print("  - 'console' - Optimized for console/terminal")
    print("  - 'unity' - Optimized for Unity console")
    
    print("\n4. Test complete!")
