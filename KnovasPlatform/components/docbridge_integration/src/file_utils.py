"""
File utilities for DocBridge AutoDoc documents.
Handles file parsing, hashing, and metadata extraction.
"""

import hashlib
from pathlib import Path
from typing import Optional, Dict
import logging

logger = logging.getLogger(__name__)


class AutoDocFileHandler:
    """Handle AutoDoc file operations and metadata extraction."""
    
    FILENAME_PATTERN = "{GUID}_{AkteID}_{Typ}.{ext}"
    
    def __init__(self, autodoc_path: Optional[str] = None):
        """
        Initialize AutoDoc file handler.
        
        Args:
            autodoc_path: Path to AutoDoc directory
        """
        from config_loader import get_config
        config = get_config()
        
        self.autodoc_path = autodoc_path or config.get('autodoc.path', '/mnt/autodoc')
        self.supported_extensions = config.get_list(
            'autodoc.supported_extensions',
            ['.docx', '.doc', '.pdf', '.txt', '.xlsx', '.xls']
        )
        self.chunk_size = config.get_int('performance.chunk_size', 8192)
        self.max_file_size = config.get_int('performance.max_file_size', 104857600)
    
    def parse_filename(self, filename: str) -> Dict[str, Optional[str]]:
        """
        Parse AutoDoc filename to extract metadata.
        
        Filename format: {GUID}_{AkteID}_{Typ}.{ext}
        Example: 12345678-ABCD_Akte4711_Brief.docx
        
        Args:
            filename: Name of file
            
        Returns:
            Dictionary with parsed components:
                - guid: Document GUID
                - akten_id: Akten ID
                - doc_type: Document type
                - extension: File extension
        """
        path = Path(filename)
        stem = path.stem
        extension = path.suffix
        
        parts = stem.split('_')
        
        result = {
            'guid': None,
            'akten_id': None,
            'doc_type': None,
            'extension': extension
        }
        
        if len(parts) >= 1:
            result['guid'] = parts[0]
        
        if len(parts) >= 2:
            result['akten_id'] = parts[1]
        
        if len(parts) >= 3:
            result['doc_type'] = '_'.join(parts[2:])
        
        return result
    
    def calculate_file_hash(
        self, 
        file_path: str, 
        algorithm: str = 'sha256'
    ) -> str:
        """
        Calculate hash of file content.
        
        Args:
            file_path: Path to file
            algorithm: Hash algorithm (sha256, md5, sha1)
            
        Returns:
            Hexadecimal hash string
        """
        if algorithm == 'md5':
            hasher = hashlib.md5()
        elif algorithm == 'sha1':
            hasher = hashlib.sha1()
        else:
            hasher = hashlib.sha256()
        
        try:
            with open(file_path, 'rb') as f:
                while True:
                    chunk = f.read(self.chunk_size)
                    if not chunk:
                        break
                    hasher.update(chunk)
            
            hash_value = hasher.hexdigest()
            logger.debug(f"Calculated {algorithm} hash for {file_path}: {hash_value}")
            return hash_value
            
        except Exception as e:
            logger.error(f"Error calculating hash for {file_path}: {e}")
            raise
    
    def is_supported_file(self, filename: str) -> bool:
        """
        Check if file has supported extension.
        
        Args:
            filename: Name of file
            
        Returns:
            True if supported, False otherwise
        """
        extension = Path(filename).suffix.lower()
        return extension in self.supported_extensions
