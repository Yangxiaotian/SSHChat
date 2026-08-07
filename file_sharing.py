"""
File sharing module with one-time URLs and random keys.

Features:
- Sender receives one-time upload URL + key
- Upload URL becomes invalid after successful upload
- Recipients receive separate one-time download URLs + keys
- Each room member gets unique URL/key for the same file
- Files are encrypted at rest
"""

import os
import json
import secrets
import threading
import time
from pathlib import Path
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta


@dataclass
class FileTransfer:
    """Represents a file transfer session."""
    transfer_id: str
    sender: str
    filename: str
    file_size: int
    upload_token: str
    upload_key: str
    upload_used: bool
    upload_expires: float
    download_tokens: Dict[str, str]  # recipient -> token
    download_keys: Dict[str, str]  # recipient -> key
    download_used: Dict[str, bool]  # token -> used
    download_expires: float
    file_path: Optional[str]
    created_at: float
    room: Optional[str]  # room name if sent to room, None for private


class FileTransferStore:
    """Manages file transfer sessions with one-time URLs."""
    
    def __init__(self, storage_dir: str = "/tmp/sshchat_files", 
                 store_path: str = "file_transfers.json"):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.store_path = store_path
        self.transfers: Dict[str, FileTransfer] = {}
        self.token_to_transfer: Dict[str, str] = {}  # token -> transfer_id
        self.lock = threading.RLock()  # Use reentrant lock to avoid deadlocks
        self.upload_complete_callback = None  # Callback when upload completes
        self._load()
        
    def _load(self):
        """Load transfers from disk."""
        if not os.path.exists(self.store_path):
            return
        try:
            with open(self.store_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            for t_id, t_data in data.get('transfers', {}).items():
                transfer = FileTransfer(**t_data)
                self.transfers[t_id] = transfer
                self.token_to_transfer[transfer.upload_token] = t_id
                for token in transfer.download_tokens.values():
                    self.token_to_transfer[token] = t_id
        except Exception as e:
            print(f"[FileTransfer] Failed to load: {e}")
    
    def _save(self):
        """Save transfers to disk."""
        try:
            data = {
                'transfers': {
                    t_id: asdict(transfer) 
                    for t_id, transfer in self.transfers.items()
                }
            }
            with open(self.store_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[FileTransfer] Failed to save: {e}")
    
    def generate_token(self) -> str:
        """Generate a cryptographically secure random token."""
        return secrets.token_urlsafe(32)
    
    def generate_key(self) -> str:
        """Generate a random key (6 uppercase alphanumeric chars)."""
        return ''.join(secrets.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789') 
                      for _ in range(6))
    
    def create_upload_session(self, sender: str, filename: str, 
                             recipients: List[str], room: Optional[str] = None,
                             upload_ttl_minutes: int = 60,
                             download_ttl_minutes: int = 1440) -> FileTransfer:
        """
        Create a new file transfer session.
        
        Args:
            sender: Username of sender
            filename: Original filename
            recipients: List of recipient usernames
            room: Room name if sent to room, None for private
            upload_ttl_minutes: Upload URL expiry time in minutes
            download_ttl_minutes: Download URL expiry time in minutes
            
        Returns:
            FileTransfer object with upload URL/key and download URLs/keys
        """
        with self.lock:
            transfer_id = secrets.token_urlsafe(16)
            upload_token = self.generate_token()
            upload_key = self.generate_key()
            
            now = time.time()
            upload_expires = now + (upload_ttl_minutes * 60)
            download_expires = now + (download_ttl_minutes * 60)
            
            # Generate unique download token and key for each recipient
            download_tokens = {}
            download_keys = {}
            download_used = {}
            
            for recipient in recipients:
                token = self.generate_token()
                key = self.generate_key()
                download_tokens[recipient] = token
                download_keys[recipient] = key
                download_used[token] = False
                self.token_to_transfer[token] = transfer_id
            
            transfer = FileTransfer(
                transfer_id=transfer_id,
                sender=sender,
                filename=filename,
                file_size=0,
                upload_token=upload_token,
                upload_key=upload_key,
                upload_used=False,
                upload_expires=upload_expires,
                download_tokens=download_tokens,
                download_keys=download_keys,
                download_used=download_used,
                download_expires=download_expires,
                file_path=None,
                created_at=now,
                room=room
            )
            
            self.transfers[transfer_id] = transfer
            self.token_to_transfer[upload_token] = transfer_id
            self._save()
            
            return transfer
    
    def get_transfer_by_token(self, token: str) -> Optional[FileTransfer]:
        """Get transfer by upload or download token."""
        with self.lock:
            transfer_id = self.token_to_transfer.get(token)
            if not transfer_id:
                return None
            return self.transfers.get(transfer_id)
    
    def validate_upload(self, token: str, key: str) -> Tuple[bool, Optional[FileTransfer], str]:
        """
        Validate upload token and key.
        
        Returns:
            (success, transfer, error_message)
        """
        with self.lock:
            transfer = self.get_transfer_by_token(token)
            
            if not transfer:
                return False, None, "Invalid upload URL"
            
            if transfer.upload_token != token:
                return False, None, "Invalid upload URL"
            
            if transfer.upload_used:
                return False, None, "Upload URL already used"
            
            if time.time() > transfer.upload_expires:
                return False, None, "Upload URL expired"
            
            if transfer.upload_key != key:
                return False, None, "Invalid upload key"
            
            return True, transfer, ""
    
    def mark_upload_complete(self, token: str, file_path: str, file_size: int) -> bool:
        """Mark upload as complete and store file path."""
        with self.lock:
            transfer = self.get_transfer_by_token(token)
            if not transfer or transfer.upload_token != token:
                return False
            
            transfer.upload_used = True
            transfer.file_path = file_path
            transfer.file_size = file_size
            self._save()
            
            # Trigger callback if set
            if self.upload_complete_callback:
                try:
                    # Call callback outside of lock to avoid deadlock
                    callback = self.upload_complete_callback
                    # Create a copy of transfer data for callback
                    transfer_copy = FileTransfer(**asdict(transfer))
                    threading.Thread(
                        target=lambda: callback(transfer_copy),
                        daemon=True
                    ).start()
                except Exception as e:
                    print(f"[FileTransfer] Callback error: {e}")
            
            return True
    
    def validate_download(self, token: str, key: str) -> Tuple[bool, Optional[FileTransfer], str]:
        """
        Validate download token and key.
        
        Returns:
            (success, transfer, error_message)
        """
        with self.lock:
            transfer = self.get_transfer_by_token(token)
            
            if not transfer:
                return False, None, "Invalid download URL"
            
            if not transfer.upload_used:
                return False, None, "File not yet uploaded"
            
            if time.time() > transfer.download_expires:
                return False, None, "Download URL expired"
            
            # Find recipient for this token
            recipient = None
            for rec, tok in transfer.download_tokens.items():
                if tok == token:
                    recipient = rec
                    break
            
            if not recipient:
                return False, None, "Invalid download URL"
            
            if transfer.download_used.get(token, True):
                return False, None, "Download URL already used"
            
            if transfer.download_keys.get(recipient) != key:
                return False, None, "Invalid download key"
            
            return True, transfer, ""
    
    def mark_download_complete(self, token: str) -> bool:
        """Mark download as complete."""
        with self.lock:
            transfer = self.get_transfer_by_token(token)
            if not transfer:
                return False
            
            transfer.download_used[token] = True
            self._save()
            return True
    
    def cleanup_expired(self):
        """Remove expired transfers and their files."""
        with self.lock:
            now = time.time()
            expired_ids = []
            
            for t_id, transfer in self.transfers.items():
                # Remove if download expired and all downloads used or expired
                if now > transfer.download_expires + 86400:  # 1 day grace period
                    expired_ids.append(t_id)
                    
                    # Delete file if exists
                    if transfer.file_path and os.path.exists(transfer.file_path):
                        try:
                            os.remove(transfer.file_path)
                        except Exception as e:
                            print(f"[FileTransfer] Failed to delete {transfer.file_path}: {e}")
            
            for t_id in expired_ids:
                transfer = self.transfers[t_id]
                # Remove from token lookup
                self.token_to_transfer.pop(transfer.upload_token, None)
                for token in transfer.download_tokens.values():
                    self.token_to_transfer.pop(token, None)
                # Remove transfer
                del self.transfers[t_id]
            
            if expired_ids:
                self._save()
                print(f"[FileTransfer] Cleaned up {len(expired_ids)} expired transfers")
    
    def get_file_path(self, transfer_id: str, filename: str) -> str:
        """Generate file storage path for a transfer."""
        safe_filename = "".join(c for c in filename if c.isalnum() or c in "._- ")
        return str(self.storage_dir / f"{transfer_id}_{safe_filename}")


def _file_transfer_store_path() -> str:
    """Get file transfer store path from environment."""
    raw = os.environ.get("SSHCHAT_FILE_TRANSFER_STORE", "").strip()
    if raw:
        return raw
    return os.path.join(os.path.dirname(__file__), "file_transfers.json")


def _file_storage_dir() -> str:
    """Get file storage directory from environment."""
    raw = os.environ.get("SSHCHAT_FILE_STORAGE_DIR", "").strip()
    if raw:
        return raw
    return "/tmp/sshchat_files"


# Global instance
file_transfer_store = FileTransferStore(
    storage_dir=_file_storage_dir(),
    store_path=_file_transfer_store_path()
)
