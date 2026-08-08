"""
File sharing module with one-time URLs and random keys.

Security model:
- Keys are delivered separately from URLs and are never placed in a URL
- The upload URL is consumed by the first successful upload
- A recipient's download URL is consumed by the first completed download
- Entering the key mints two short-lived, single-use tickets: one for preview
  and one for download. The bytes are only ever served from a ticket URL, and
  a ticket dies on first use, so capturing a URL off the wire buys nothing.
- Each room member gets a unique URL and key for the same file
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


# A download URL is consumed by the first completed download. Set to 0 only if
# you knowingly want recipients to be able to fetch a file more than once.
ONE_TIME_DOWNLOAD = os.environ.get("SSHCHAT_ONE_TIME_DOWNLOAD", "1").strip() != "0"

# How long a minted preview/download ticket stays valid before the recipient
# has to re-enter the key. Tickets are single-use regardless of this window.
TICKET_TTL_SECONDS = int(os.environ.get("SSHCHAT_TICKET_TTL_SECONDS", "600"))


def sanitize_filename(filename: str) -> str:
    """Reduce a client-supplied filename to a bare, displayable name."""
    raw = str(filename or "").replace("\\", "/")
    name = os.path.basename(raw).replace("\x00", "").strip()
    if name in ("", ".", ".."):
        return "file"
    return name[:200]


@dataclass
class FileTicket:
    """A single-use, short-lived permit to fetch the bytes of one transfer."""
    ticket: str
    transfer_id: str
    recipient: str
    kind: str  # 'preview' or 'download'
    used: bool
    expires: float


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
        self.tickets: Dict[str, FileTicket] = {}  # ticket -> FileTicket
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
            for ticket, tk_data in data.get('tickets', {}).items():
                self.tickets[ticket] = FileTicket(**tk_data)
        except Exception as e:
            print(f"[FileTransfer] Failed to load: {e}")
    
    def _save(self):
        """Save transfers to disk."""
        try:
            data = {
                'transfers': {
                    t_id: asdict(transfer) 
                    for t_id, transfer in self.transfers.items()
                },
                'tickets': {
                    ticket: asdict(t) for ticket, t in self.tickets.items()
                },
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
    
    def create_upload_session(self, sender: str,
                             recipients: List[str], filename: str = "",
                             room: Optional[str] = None,
                             upload_ttl_minutes: int = 60,
                             download_ttl_minutes: int = 1440) -> FileTransfer:
        """
        Create a new file transfer session.
        
        Args:
            sender: Username of sender
            recipients: List of recipient usernames
            filename: Optional placeholder; the real name comes from the upload
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
                filename=sanitize_filename(filename) if filename else "",
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
                return False, None, "上传链接无效"
            
            if transfer.upload_token != token:
                return False, None, "上传链接无效"
            
            if transfer.upload_used:
                return False, None, "该上传链接已使用过"
            
            if time.time() > transfer.upload_expires:
                return False, None, "上传链接已过期"
            
            if transfer.upload_key != key:
                return False, None, "上传密钥不正确"
            
            return True, transfer, ""
    
    def mark_upload_complete(self, token: str, file_path: str, file_size: int,
                             filename: Optional[str] = None) -> bool:
        """Mark upload as complete and store file path, size and real filename."""
        with self.lock:
            transfer = self.get_transfer_by_token(token)
            if not transfer or transfer.upload_token != token:
                return False
            
            if filename:
                transfer.filename = sanitize_filename(filename)
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
                return False, None, "下载链接无效"
            
            if not transfer.upload_used:
                return False, None, "发送方还没有上传文件"
            
            if time.time() > transfer.download_expires:
                return False, None, "下载链接已过期"
            
            # Find recipient for this token
            recipient = None
            for rec, tok in transfer.download_tokens.items():
                if tok == token:
                    recipient = rec
                    break
            
            if not recipient:
                return False, None, "下载链接无效"
            
            if ONE_TIME_DOWNLOAD and transfer.download_used.get(token, False):
                return False, None, "该下载链接已使用过"
            
            if transfer.download_keys.get(recipient) != key:
                return False, None, "下载密钥不正确"
            
            return True, transfer, ""
    
    def _recipient_for_token(self, transfer: FileTransfer, token: str) -> Optional[str]:
        for recipient, tok in transfer.download_tokens.items():
            if tok == token:
                return recipient
        return None
    
    def issue_tickets(self, token: str, key: str) -> Tuple[Optional[FileTransfer], Dict[str, str], str]:
        """Exchange a correct key for fresh single-use preview/download tickets.
        
        Returns:
            (transfer, {'preview': ticket, 'download': ticket}, error_message)
        """
        with self.lock:
            valid, transfer, error = self.validate_download(token, key)
            if not valid:
                return None, {}, error
            
            recipient = self._recipient_for_token(transfer, token)
            if not recipient:
                return None, {}, "下载链接无效"
            
            # Anything minted earlier for this recipient is retired, so an old
            # link that was observed but never used cannot be replayed later.
            self.revoke_tickets(transfer.transfer_id, recipient)
            
            now = time.time()
            issued: Dict[str, str] = {}
            for kind in ("preview", "download"):
                ticket = self.generate_token()
                self.tickets[ticket] = FileTicket(
                    ticket=ticket,
                    transfer_id=transfer.transfer_id,
                    recipient=recipient,
                    kind=kind,
                    used=False,
                    expires=now + TICKET_TTL_SECONDS,
                )
                issued[kind] = ticket
            
            self._save()
            return transfer, issued, ""
    
    def revoke_tickets(self, transfer_id: str, recipient: str) -> None:
        """Drop every outstanding ticket belonging to one recipient."""
        with self.lock:
            stale = [
                ticket for ticket, t in self.tickets.items()
                if t.transfer_id == transfer_id and t.recipient == recipient
            ]
            for ticket in stale:
                del self.tickets[ticket]
    
    def consume_ticket(self, ticket: str) -> Tuple[Optional[FileTransfer], Optional[FileTicket], str]:
        """Burn a ticket and hand back the transfer it unlocks.
        
        The ticket is marked used before any bytes are served, so replaying a
        captured URL always fails. The recipient's download token is only
        retired once the transfer actually finishes, which lets a dropped
        connection be retried by re-entering the key.
        """
        with self.lock:
            entry = self.tickets.get(ticket)
            if entry is None or entry.used:
                return None, None, "链接无效或已被使用"
            
            if time.time() > entry.expires:
                del self.tickets[ticket]
                self._save()
                return None, None, "链接已过期，请回到页面重新输入密钥"
            
            transfer = self.transfers.get(entry.transfer_id)
            if transfer is None or not transfer.upload_used:
                return None, None, "文件不存在"
            
            entry.used = True
            self._save()
            return transfer, entry, ""
    
    def download_token_for(self, transfer: FileTransfer, recipient: str) -> Optional[str]:
        """Look up the download token issued to one recipient."""
        with self.lock:
            return transfer.download_tokens.get(recipient)

    def _match_recipient(self, transfer: FileTransfer, recipient: str) -> Optional[str]:
        key = (recipient or "").strip().lower()
        if not key:
            return None
        for name in transfer.download_tokens:
            if name.lower() == key:
                return name
        return None

    def revoke_recipient(self, transfer_id: str, recipient: str) -> bool:
        """Invalidate one recipient's download access (e.g. /leave recall of a file)."""
        with self.lock:
            transfer = self.transfers.get(transfer_id)
            if not transfer:
                return False
            match = self._match_recipient(transfer, recipient)
            if match is None:
                return False
            token = transfer.download_tokens.pop(match, None)
            transfer.download_keys.pop(match, None)
            if token:
                transfer.download_used.pop(token, None)
                self.token_to_transfer.pop(token, None)
            self.revoke_tickets(transfer_id, match)
            self._save()
            return True
    
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
            
            spent = [
                ticket for ticket, t in self.tickets.items()
                if t.used or now > t.expires
            ]
            for ticket in spent:
                del self.tickets[ticket]
            
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
                for ticket in [k for k, t in self.tickets.items() if t.transfer_id == t_id]:
                    del self.tickets[ticket]
                # Remove transfer
                del self.transfers[t_id]
            
            if expired_ids or spent:
                self._save()
            if expired_ids:
                print(f"[FileTransfer] Cleaned up {len(expired_ids)} expired transfers")
    
    def get_file_path(self, transfer_id: str, filename: str) -> str:
        """Generate file storage path for a transfer."""
        safe_filename = "".join(
            c for c in sanitize_filename(filename) if c.isalnum() or c in "._- "
        ).strip()
        if not safe_filename:
            safe_filename = "file"
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
