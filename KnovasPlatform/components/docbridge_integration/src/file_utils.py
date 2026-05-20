"""
File utilities for DocBridge AutoDoc documents.
Handles file parsing, hashing, and metadata extraction.
"""

import os
import hashlib
import mimetypes
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
import logging
from datetime import datetime

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
    
    def get_file_info(self, file_path: str) -> Dict[str, Any]:
        """
        Get comprehensive file information.
        
        Args:
            file_path: Path to file
            
        Returns:
            Dictionary with file information
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")
        
        stat = os.stat(file_path)
        filename = os.path.basename(file_path)
        parsed = self.parse_filename(filename)
        
        mime_type, _ = mimetypes.guess_type(file_path)
        
        file_info = {
            'path': file_path,
            'filename': filename,
            'size': stat.st_size,
            'created': datetime.fromtimestamp(stat.st_ctime),
            'modified': datetime.fromtimestamp(stat.st_mtime),
            'mime_type': mime_type,
            'extension': parsed['extension'],
            'guid': parsed['guid'],
            'akten_id': parsed['akten_id'],
            'doc_type': parsed['doc_type'],
            'hash_sha256': None,
            'hash_md5': None
        }
        
        if stat.st_size <= self.max_file_size:
            try:
                file_info['hash_sha256'] = self.calculate_file_hash(file_path, 'sha256')
                file_info['hash_md5'] = self.calculate_file_hash(file_path, 'md5')
            except Exception as e:
                logger.warning(f"Could not calculate hashes for {file_path}: {e}")
        else:
            logger.warning(
                f"File {file_path} exceeds max size ({stat.st_size} > {self.max_file_size}), "
                "skipping hash calculation"
            )
        
        return file_info
    
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
    
    def find_autodoc_files(
        self, 
        pattern: Optional[str] = None,
        recursive: bool = True
    ) -> list:
        """
        Find all AutoDoc files matching pattern.
        
        Args:
            pattern: Glob pattern (e.g., '*.docx'). If None, finds all supported files.
            recursive: Search recursively in subdirectories
            
        Returns:
            List of file paths
        """
        if not os.path.exists(self.autodoc_path):
            logger.error(f"AutoDoc path does not exist: {self.autodoc_path}")
            return []
        
        autodoc_path = Path(self.autodoc_path)
        files = []
        
        if pattern:
            if recursive:
                files = list(autodoc_path.rglob(pattern))
            else:
                files = list(autodoc_path.glob(pattern))
        else:
            for ext in self.supported_extensions:
                pattern_str = f"*{ext}"
                if recursive:
                    files.extend(autodoc_path.rglob(pattern_str))
                else:
                    files.extend(autodoc_path.glob(pattern_str))
        
        file_paths = [str(f) for f in files if f.is_file()]
        logger.info(f"Found {len(file_paths)} AutoDoc files")
        return file_paths
    
    def validate_file(self, file_path: str) -> Tuple[bool, Optional[str]]:
        """
        Validate file for processing.
        
        Args:
            file_path: Path to file
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        if not os.path.exists(file_path):
            return False, "File does not exist"
        
        if not os.path.isfile(file_path):
            return False, "Path is not a file"
        
        stat = os.stat(file_path)
        
        if stat.st_size == 0:
            return False, "File is empty"
        
        if stat.st_size > self.max_file_size:
            return False, f"File size ({stat.st_size}) exceeds maximum ({self.max_file_size})"
        
        filename = os.path.basename(file_path)
        if not self.is_supported_file(filename):
            return False, f"Unsupported file type: {Path(filename).suffix}"
        
        return True, None
    
    def get_relative_path(self, file_path: str) -> str:
        """
        Get relative path from AutoDoc root.
        
        Args:
            file_path: Absolute file path
            
        Returns:
            Relative path from AutoDoc directory
        """
        try:
            return os.path.relpath(file_path, self.autodoc_path)
        except ValueError:
            return file_path
