"""
HTTP server for file uploads and downloads with one-time URLs.

Provides:
- Upload endpoint: POST /upload/<token>?key=<key>
- Download endpoint: GET /download/<token>?key=<key>
- HTTPS support with auto-generated or provided certificates
"""

import os
import cgi
import json
import ssl
import subprocess
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from pathlib import Path
from typing import Optional
import file_sharing


MAX_FILE_SIZE = int(os.environ.get("SSHCHAT_MAX_FILE_SIZE", str(100 * 1024 * 1024)))  # 100MB default


class FileTransferHandler(BaseHTTPRequestHandler):
    """HTTP request handler for file uploads and downloads."""
    
    def log_message(self, format, *args):
        """Override to customize logging."""
        print(f"[FileHTTP] {self.address_string()} - {format % args}")
    
    def _send_json_response(self, code: int, data: dict):
        """Send JSON response."""
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))
    
    def _send_error_json(self, code: int, message: str):
        """Send JSON error response."""
        self._send_json_response(code, {'error': message})
    
    def do_OPTIONS(self):
        """Handle CORS preflight."""
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
    
    def do_POST(self):
        """Handle file upload."""
        parsed = urlparse(self.path)
        path_parts = parsed.path.strip('/').split('/')
        
        if len(path_parts) != 2 or path_parts[0] != 'upload':
            self._send_error_json(404, "Not found")
            return
        
        token = path_parts[1]
        query_params = parse_qs(parsed.query)
        key = query_params.get('key', [''])[0]
        
        if not key:
            self._send_error_json(400, "Missing key parameter")
            return
        
        # Validate token and key
        store = file_sharing.file_transfer_store
        valid, transfer, error = store.validate_upload(token, key)
        
        if not valid:
            self._send_error_json(403, error)
            return
        
        # Parse multipart form data
        content_type = self.headers.get('Content-Type', '')
        if not content_type.startswith('multipart/form-data'):
            self._send_error_json(400, "Content-Type must be multipart/form-data")
            return
        
        try:
            # Parse form data
            form = cgi.FieldStorage(
                fp=self.rfile,
                headers=self.headers,
                environ={
                    'REQUEST_METHOD': 'POST',
                    'CONTENT_TYPE': content_type,
                }
            )
            
            if 'file' not in form:
                self._send_error_json(400, "No file uploaded")
                return
            
            file_item = form['file']
            if not file_item.file:
                self._send_error_json(400, "No file content")
                return
            
            # Check file size
            file_item.file.seek(0, 2)  # Seek to end
            file_size = file_item.file.tell()
            file_item.file.seek(0)  # Seek back to start
            
            if file_size > MAX_FILE_SIZE:
                self._send_error_json(413, f"File too large (max {MAX_FILE_SIZE / 1024 / 1024:.0f}MB)")
                return
            
            if file_size == 0:
                self._send_error_json(400, "Empty file")
                return
            
            # Save file
            file_path = store.get_file_path(transfer.transfer_id, transfer.filename)
            with open(file_path, 'wb') as f:
                while True:
                    chunk = file_item.file.read(8192)
                    if not chunk:
                        break
                    f.write(chunk)
            
            # Mark upload complete
            store.mark_upload_complete(token, file_path, file_size)
            
            self._send_json_response(200, {
                'success': True,
                'message': 'File uploaded successfully',
                'filename': transfer.filename,
                'size': file_size
            })
            
        except Exception as e:
            print(f"[FileHTTP] Upload error: {e}")
            self._send_error_json(500, "Upload failed")
    
    def do_GET(self):
        """Handle file download."""
        parsed = urlparse(self.path)
        path_parts = parsed.path.strip('/').split('/')
        
        if len(path_parts) != 2 or path_parts[0] != 'download':
            self._send_error_json(404, "Not found")
            return
        
        token = path_parts[1]
        query_params = parse_qs(parsed.query)
        key = query_params.get('key', [''])[0]
        
        if not key:
            self._send_error_json(400, "Missing key parameter")
            return
        
        # Validate token and key
        store = file_sharing.file_transfer_store
        valid, transfer, error = store.validate_download(token, key)
        
        if not valid:
            self._send_error_json(403, error)
            return
        
        if not transfer.file_path or not os.path.exists(transfer.file_path):
            self._send_error_json(404, "File not found")
            return
        
        try:
            # Send file
            self.send_response(200)
            self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('Content-Disposition', 
                           f'attachment; filename="{transfer.filename}"')
            self.send_header('Content-Length', str(transfer.file_size))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            with open(transfer.file_path, 'rb') as f:
                while True:
                    chunk = f.read(8192)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
            
            # Mark download complete
            store.mark_download_complete(token)
            
        except Exception as e:
            print(f"[FileHTTP] Download error: {e}")


class FileHTTPServer:
    """HTTP/HTTPS server for file transfers."""
    
    def __init__(self, host: str = "0.0.0.0", port: int = 8443,
                 use_https: bool = True, cert_file: Optional[str] = None,
                 key_file: Optional[str] = None, domain: Optional[str] = None):
        self.host = host
        self.port = port
        self.use_https = use_https
        self.cert_file = cert_file
        self.key_file = key_file
        self.domain = domain
        self.server: Optional[HTTPServer] = None
        self.thread: Optional[threading.Thread] = None
        
    def setup_certificates(self):
        """Setup SSL certificates (generate self-signed or use Let's Encrypt)."""
        if self.cert_file and self.key_file:
            if os.path.exists(self.cert_file) and os.path.exists(self.key_file):
                print(f"[FileHTTP] Using provided certificates: {self.cert_file}, {self.key_file}")
                return True
        
        # Try Let's Encrypt if domain is provided
        if self.domain:
            print(f"[FileHTTP] Attempting to obtain Let's Encrypt certificate for {self.domain}")
            success = self._obtain_letsencrypt_cert()
            if success:
                return True
            print("[FileHTTP] Let's Encrypt failed, falling back to self-signed")
        
        # Generate self-signed certificate
        return self._generate_self_signed_cert()
    
    def _obtain_letsencrypt_cert(self) -> bool:
        """Obtain certificate from Let's Encrypt using certbot."""
        if not self.domain:
            return False
        
        # Check if certbot is available
        try:
            subprocess.run(['certbot', '--version'], 
                         capture_output=True, check=True, timeout=5)
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            print("[FileHTTP] certbot not found")
            return False
        
        # Try to obtain certificate
        cert_dir = Path.home() / '.sshchat_certs'
        cert_dir.mkdir(parents=True, exist_ok=True)
        
        try:
            # Use certbot standalone mode
            cmd = [
                'sudo', 'certbot', 'certonly',
                '--standalone',
                '--non-interactive',
                '--agree-tos',
                '--register-unsafely-without-email',
                '-d', self.domain,
                '--http-01-port', '80'
            ]
            
            result = subprocess.run(cmd, capture_output=True, timeout=120)
            
            if result.returncode == 0:
                # Certbot stores certs in /etc/letsencrypt/live/<domain>/
                cert_path = f"/etc/letsencrypt/live/{self.domain}/fullchain.pem"
                key_path = f"/etc/letsencrypt/live/{self.domain}/privkey.pem"
                
                if os.path.exists(cert_path) and os.path.exists(key_path):
                    self.cert_file = cert_path
                    self.key_file = key_path
                    print(f"[FileHTTP] Let's Encrypt certificate obtained: {cert_path}")
                    return True
            
            print(f"[FileHTTP] certbot failed: {result.stderr.decode()}")
            
        except Exception as e:
            print(f"[FileHTTP] Let's Encrypt error: {e}")
        
        return False
    
    def _generate_self_signed_cert(self) -> bool:
        """Generate self-signed SSL certificate."""
        cert_dir = Path.home() / '.sshchat_certs'
        cert_dir.mkdir(parents=True, exist_ok=True)
        
        cert_path = cert_dir / 'server.crt'
        key_path = cert_dir / 'server.key'
        
        if cert_path.exists() and key_path.exists():
            self.cert_file = str(cert_path)
            self.key_file = str(key_path)
            print(f"[FileHTTP] Using existing self-signed certificate: {cert_path}")
            return True
        
        try:
            # Generate self-signed certificate
            cmd = [
                'openssl', 'req', '-x509', '-newkey', 'rsa:4096',
                '-keyout', str(key_path),
                '-out', str(cert_path),
                '-days', '365',
                '-nodes',
                '-subj', f'/CN={self.domain or "localhost"}'
            ]
            
            result = subprocess.run(cmd, capture_output=True, timeout=30)
            
            if result.returncode == 0 and cert_path.exists() and key_path.exists():
                self.cert_file = str(cert_path)
                self.key_file = str(key_path)
                print(f"[FileHTTP] Generated self-signed certificate: {cert_path}")
                print("[FileHTTP] WARNING: Self-signed certificates will show security warnings in browsers")
                return True
            
            print(f"[FileHTTP] openssl failed: {result.stderr.decode()}")
            
        except Exception as e:
            print(f"[FileHTTP] Self-signed cert generation error: {e}")
        
        return False
    
    def start(self):
        """Start the HTTP/HTTPS server in a background thread."""
        if self.server:
            print("[FileHTTP] Server already running")
            return
        
        if self.use_https:
            if not self.setup_certificates():
                print("[FileHTTP] Failed to setup certificates, falling back to HTTP")
                self.use_https = False
        
        self.server = HTTPServer((self.host, self.port), FileTransferHandler)
        
        if self.use_https and self.cert_file and self.key_file:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(self.cert_file, self.key_file)
            self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
            protocol = "HTTPS"
        else:
            protocol = "HTTP"
        
        print(f"[FileHTTP] Starting {protocol} server on {self.host}:{self.port}")
        
        self.thread = threading.Thread(target=self._run_server, daemon=True)
        self.thread.start()
    
    def _run_server(self):
        """Run the server (called in background thread)."""
        try:
            self.server.serve_forever()
        except Exception as e:
            print(f"[FileHTTP] Server error: {e}")
    
    def stop(self):
        """Stop the HTTP server."""
        if self.server:
            print("[FileHTTP] Stopping server")
            self.server.shutdown()
            self.server = None
            self.thread = None
    
    def get_base_url(self) -> str:
        """Get base URL for this server."""
        protocol = "https" if self.use_https else "http"
        if self.domain:
            return f"{protocol}://{self.domain}:{self.port}"
        return f"{protocol}://{self.host}:{self.port}"


def create_file_server() -> FileHTTPServer:
    """Create and configure file HTTP server from environment."""
    host = os.environ.get("SSHCHAT_FILE_HTTP_HOST", "0.0.0.0")
    port = int(os.environ.get("SSHCHAT_FILE_HTTP_PORT", "8443"))
    use_https = os.environ.get("SSHCHAT_FILE_USE_HTTPS", "1") != "0"
    cert_file = os.environ.get("SSHCHAT_FILE_CERT_FILE", "").strip() or None
    key_file = os.environ.get("SSHCHAT_FILE_KEY_FILE", "").strip() or None
    domain = os.environ.get("SSHCHAT_FILE_DOMAIN", "").strip() or None
    
    return FileHTTPServer(
        host=host,
        port=port,
        use_https=use_https,
        cert_file=cert_file,
        key_file=key_file,
        domain=domain
    )
