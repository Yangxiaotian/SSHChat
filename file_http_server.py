"""
HTTP server for file uploads and downloads with one-time URLs.

Provides:
- Upload page: GET /upload/<token> - Shows HTML form with key input
- Upload endpoint: POST /upload/<token> - Handles file upload with key
- Download page: GET /download/<token> - Shows HTML page with key input and preview
- Download file: GET /download/<token>/file?key=<key> - Direct file download
- HTTPS support with auto-generated or provided certificates
"""

import os
import cgi
import json
import ssl
import subprocess
import threading
import mimetypes
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, quote
from pathlib import Path
from typing import Optional
import file_sharing


MAX_FILE_SIZE = int(os.environ.get("SSHCHAT_MAX_FILE_SIZE", str(100 * 1024 * 1024)))  # 100MB default

# File types that can be previewed in browser
PREVIEWABLE_TYPES = {
    # Images
    'image/jpeg', 'image/jpg', 'image/png', 'image/gif', 'image/webp', 'image/svg+xml', 'image/bmp',
    # Videos
    'video/mp4', 'video/webm', 'video/ogg',
    # Audio
    'audio/mpeg', 'audio/mp3', 'audio/wav', 'audio/ogg', 'audio/webm',
    # Documents
    'application/pdf',
    # Text
    'text/plain', 'text/html', 'text/css', 'text/javascript', 'text/markdown',
    'application/json', 'application/xml',
}


def get_mime_type(filename: str) -> str:
    """Get MIME type for a file."""
    mime_type, _ = mimetypes.guess_type(filename)
    return mime_type or 'application/octet-stream'


def is_previewable(mime_type: str) -> bool:
    """Check if a file type can be previewed in browser."""
    return mime_type in PREVIEWABLE_TYPES


def generate_upload_page(token: str, error: str = "") -> str:
    """Generate HTML upload page."""
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>文件上传 - SSHChat</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }}
        .container {{
            background: white;
            border-radius: 16px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            padding: 40px;
            max-width: 500px;
            width: 100%;
        }}
        h1 {{
            color: #333;
            margin-bottom: 10px;
            font-size: 28px;
        }}
        .subtitle {{
            color: #666;
            margin-bottom: 30px;
            font-size: 14px;
        }}
        .form-group {{
            margin-bottom: 20px;
        }}
        label {{
            display: block;
            margin-bottom: 8px;
            color: #555;
            font-weight: 500;
        }}
        input[type="text"], input[type="file"] {{
            width: 100%;
            padding: 12px;
            border: 2px solid #e0e0e0;
            border-radius: 8px;
            font-size: 14px;
            transition: border-color 0.3s;
        }}
        input[type="text"]:focus, input[type="file"]:focus {{
            outline: none;
            border-color: #667eea;
        }}
        input[type="text"] {{
            text-transform: uppercase;
            letter-spacing: 2px;
            font-family: monospace;
            font-size: 18px;
        }}
        .file-input-wrapper {{
            position: relative;
            overflow: hidden;
        }}
        button {{
            width: 100%;
            padding: 14px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            border-radius: 8px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            transition: transform 0.2s, box-shadow 0.2s;
        }}
        button:hover {{
            transform: translateY(-2px);
            box-shadow: 0 8px 20px rgba(102, 126, 234, 0.4);
        }}
        button:active {{
            transform: translateY(0);
        }}
        button:disabled {{
            background: #ccc;
            cursor: not-allowed;
            transform: none;
        }}
        .error {{
            background: #fee;
            border: 1px solid #fcc;
            color: #c33;
            padding: 12px;
            border-radius: 8px;
            margin-bottom: 20px;
            font-size: 14px;
        }}
        .info {{
            background: #e3f2fd;
            border: 1px solid #90caf9;
            color: #1976d2;
            padding: 12px;
            border-radius: 8px;
            margin-bottom: 20px;
            font-size: 13px;
            line-height: 1.6;
        }}
        .selected-file {{
            margin-top: 10px;
            padding: 10px;
            background: #f5f5f5;
            border-radius: 6px;
            font-size: 13px;
            color: #666;
        }}
        .progress {{
            display: none;
            margin-top: 20px;
        }}
        .progress-bar {{
            width: 100%;
            height: 8px;
            background: #e0e0e0;
            border-radius: 4px;
            overflow: hidden;
        }}
        .progress-fill {{
            height: 100%;
            background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
            transition: width 0.3s;
            width: 0%;
        }}
        .progress-text {{
            text-align: center;
            margin-top: 8px;
            font-size: 13px;
            color: #666;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>🔒 安全文件上传</h1>
        <p class="subtitle">一次性上传链接，上传后立即失效</p>
        
        {"<div class='error'>" + error + "</div>" if error else ""}
        
        <div class="info">
            ℹ️ <strong>使用说明：</strong><br>
            1. 输入您收到的6位上传密钥<br>
            2. 选择要上传的文件<br>
            3. 点击上传按钮<br>
            4. 上传成功后此链接将立即失效
        </div>
        
        <form id="uploadForm" method="POST" enctype="multipart/form-data">
            <div class="form-group">
                <label for="key">上传密钥 *</label>
                <input type="text" id="key" name="key" required 
                       placeholder="输入6位密钥" 
                       maxlength="6" 
                       pattern="[A-Z0-9]{{6}}"
                       autocomplete="off">
            </div>
            
            <div class="form-group">
                <label for="file">选择文件 *</label>
                <input type="file" id="file" name="file" required>
                <div id="selectedFile" class="selected-file" style="display:none;"></div>
            </div>
            
            <button type="submit" id="uploadBtn">📤 开始上传</button>
        </form>
        
        <div class="progress" id="progress">
            <div class="progress-bar">
                <div class="progress-fill" id="progressFill"></div>
            </div>
            <div class="progress-text" id="progressText">上传中...</div>
        </div>
    </div>
    
    <script>
        const form = document.getElementById('uploadForm');
        const fileInput = document.getElementById('file');
        const keyInput = document.getElementById('key');
        const uploadBtn = document.getElementById('uploadBtn');
        const selectedFile = document.getElementById('selectedFile');
        const progress = document.getElementById('progress');
        const progressFill = document.getElementById('progressFill');
        const progressText = document.getElementById('progressText');
        
        // Auto uppercase key input
        keyInput.addEventListener('input', function(e) {{
            this.value = this.value.toUpperCase().replace(/[^A-Z0-9]/g, '');
        }});
        
        // Show selected file
        fileInput.addEventListener('change', function(e) {{
            if (this.files.length > 0) {{
                const file = this.files[0];
                const size = (file.size / 1024 / 1024).toFixed(2);
                selectedFile.textContent = `📄 ${{file.name}} (${{size}} MB)`;
                selectedFile.style.display = 'block';
            }}
        }});
        
        form.addEventListener('submit', async function(e) {{
            e.preventDefault();
            
            const key = keyInput.value.trim();
            const file = fileInput.files[0];
            
            if (!key || key.length !== 6) {{
                alert('请输入6位密钥');
                return;
            }}
            
            if (!file) {{
                alert('请选择文件');
                return;
            }}
            
            // Disable form
            uploadBtn.disabled = true;
            uploadBtn.textContent = '上传中...';
            progress.style.display = 'block';
            
            const formData = new FormData();
            formData.append('file', file);
            
            try {{
                const response = await fetch('?key=' + encodeURIComponent(key), {{
                    method: 'POST',
                    body: formData
                }});
                
                const result = await response.json();
                
                if (response.ok) {{
                    progressFill.style.width = '100%';
                    progressText.textContent = '✅ 上传成功！';
                    progressText.style.color = '#4caf50';
                    
                    setTimeout(() => {{
                        alert('上传成功！\\n\\n接收者将收到下载通知。\\n\\n此上传链接已失效。');
                        window.location.reload();
                    }}, 1000);
                }} else {{
                    throw new Error(result.error || '上传失败');
                }}
            }} catch (error) {{
                progressText.textContent = '❌ ' + error.message;
                progressText.style.color = '#f44336';
                uploadBtn.disabled = false;
                uploadBtn.textContent = '📤 重新上传';
            }}
        }});
    </script>
</body>
</html>"""


def generate_download_page(token: str, filename: str, file_size: int, mime_type: str) -> str:
    """Generate HTML download page with preview support."""
    can_preview = is_previewable(mime_type)
    size_mb = file_size / 1024 / 1024
    
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>文件下载 - {filename}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            background: linear-gradient(135deg, #11998e 0%, #38ef7d 100%);
            min-height: 100vh;
            padding: 20px;
        }}
        .container {{
            max-width: 900px;
            margin: 0 auto;
            background: white;
            border-radius: 16px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            overflow: hidden;
        }}
        .header {{
            background: linear-gradient(135deg, #11998e 0%, #38ef7d 100%);
            color: white;
            padding: 30px;
        }}
        h1 {{
            font-size: 28px;
            margin-bottom: 10px;
        }}
        .subtitle {{
            opacity: 0.9;
            font-size: 14px;
        }}
        .content {{
            padding: 40px;
        }}
        .file-info {{
            background: #f5f5f5;
            padding: 20px;
            border-radius: 12px;
            margin-bottom: 30px;
        }}
        .file-info-row {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 8px 0;
        }}
        .file-info-label {{
            color: #666;
            font-size: 14px;
        }}
        .file-info-value {{
            color: #333;
            font-weight: 600;
            font-size: 14px;
        }}
        .form-group {{
            margin-bottom: 20px;
        }}
        label {{
            display: block;
            margin-bottom: 8px;
            color: #555;
            font-weight: 500;
        }}
        input[type="text"] {{
            width: 100%;
            padding: 14px;
            border: 2px solid #e0e0e0;
            border-radius: 8px;
            font-size: 18px;
            text-transform: uppercase;
            letter-spacing: 2px;
            font-family: monospace;
            transition: border-color 0.3s;
        }}
        input[type="text"]:focus {{
            outline: none;
            border-color: #11998e;
        }}
        button {{
            width: 100%;
            padding: 14px;
            background: linear-gradient(135deg, #11998e 0%, #38ef7d 100%);
            color: white;
            border: none;
            border-radius: 8px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            transition: transform 0.2s, box-shadow 0.2s;
        }}
        button:hover {{
            transform: translateY(-2px);
            box-shadow: 0 8px 20px rgba(17, 153, 142, 0.4);
        }}
        button:active {{
            transform: translateY(0);
        }}
        button:disabled {{
            background: #ccc;
            cursor: not-allowed;
            transform: none;
        }}
        .error {{
            background: #fee;
            border: 1px solid #fcc;
            color: #c33;
            padding: 12px;
            border-radius: 8px;
            margin-bottom: 20px;
            font-size: 14px;
        }}
        .info {{
            background: #e8f5e9;
            border: 1px solid #a5d6a7;
            color: #2e7d32;
            padding: 12px;
            border-radius: 8px;
            margin-bottom: 20px;
            font-size: 13px;
            line-height: 1.6;
        }}
        .preview-container {{
            display: none;
            margin-top: 30px;
            padding: 20px;
            background: #f9f9f9;
            border-radius: 12px;
        }}
        .preview-container.show {{
            display: block;
        }}
        .preview-title {{
            font-size: 18px;
            font-weight: 600;
            margin-bottom: 15px;
            color: #333;
        }}
        .preview-content {{
            background: white;
            padding: 20px;
            border-radius: 8px;
            text-align: center;
        }}
        .preview-content img {{
            max-width: 100%;
            height: auto;
            border-radius: 8px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.1);
        }}
        .preview-content video, .preview-content audio {{
            max-width: 100%;
            border-radius: 8px;
        }}
        .preview-content iframe {{
            width: 100%;
            height: 600px;
            border: none;
            border-radius: 8px;
        }}
        .preview-content pre {{
            text-align: left;
            background: #f5f5f5;
            padding: 15px;
            border-radius: 8px;
            overflow-x: auto;
            max-height: 500px;
        }}
        .download-button {{
            margin-top: 15px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📥 安全文件下载</h1>
            <p class="subtitle">一次性下载链接，下载后立即失效</p>
        </div>
        
        <div class="content">
            <div class="file-info">
                <div class="file-info-row">
                    <span class="file-info-label">📄 文件名：</span>
                    <span class="file-info-value">{filename}</span>
                </div>
                <div class="file-info-row">
                    <span class="file-info-label">📦 文件大小：</span>
                    <span class="file-info-value">{size_mb:.2f} MB</span>
                </div>
                <div class="file-info-row">
                    <span class="file-info-label">🔖 文件类型：</span>
                    <span class="file-info-value">{mime_type}</span>
                </div>
                <div class="file-info-row">
                    <span class="file-info-label">👁️ 在线预览：</span>
                    <span class="file-info-value">{'✅ 支持' if can_preview else '❌ 不支持'}</span>
                </div>
            </div>
            
            <div class="info">
                ℹ️ <strong>使用说明：</strong><br>
                1. 输入您收到的6位下载密钥<br>
                {'2. 系统将自动预览文件（如支持）<br>3. 点击下载按钮获取文件<br>4. 下载后此链接将立即失效' if can_preview else '2. 点击下载按钮获取文件<br>3. 下载后此链接将立即失效'}
            </div>
            
            <form id="downloadForm">
                <div class="form-group">
                    <label for="key">下载密钥 *</label>
                    <input type="text" id="key" name="key" required 
                           placeholder="输入6位密钥" 
                           maxlength="6" 
                           pattern="[A-Z0-9]{{6}}"
                           autocomplete="off">
                </div>
                
                <button type="submit" id="submitBtn">
                    {'🔍 验证并预览' if can_preview else '📥 验证并下载'}
                </button>
            </form>
            
            <div class="preview-container" id="previewContainer">
                <div class="preview-title">📋 文件预览</div>
                <div class="preview-content" id="previewContent"></div>
                <button class="download-button" id="downloadBtn" onclick="downloadFile()">
                    💾 下载文件
                </button>
            </div>
        </div>
    </div>
    
    <script>
        const form = document.getElementById('downloadForm');
        const keyInput = document.getElementById('key');
        const submitBtn = document.getElementById('submitBtn');
        const previewContainer = document.getElementById('previewContainer');
        const previewContent = document.getElementById('previewContent');
        const canPreview = {str(can_preview).lower()};
        const mimeType = '{mime_type}';
        const filename = '{filename}';
        let downloadKey = '';
        
        // Auto uppercase key input
        keyInput.addEventListener('input', function(e) {{
            this.value = this.value.toUpperCase().replace(/[^A-Z0-9]/g, '');
        }});
        
        form.addEventListener('submit', async function(e) {{
            e.preventDefault();
            
            const key = keyInput.value.trim();
            
            if (!key || key.length !== 6) {{
                alert('请输入6位密钥');
                return;
            }}
            
            downloadKey = key;
            
            if (canPreview) {{
                await loadPreview(key);
            }} else {{
                downloadFile();
            }}
        }});
        
        async function loadPreview(key) {{
            submitBtn.disabled = true;
            submitBtn.textContent = '加载中...';
            
            try {{
                const fileUrl = `file?key=${{encodeURIComponent(key)}}`;
                
                if (mimeType.startsWith('image/')) {{
                    previewContent.innerHTML = `<img src="${{fileUrl}}" alt="${{filename}}">`;
                }} else if (mimeType.startsWith('video/')) {{
                    previewContent.innerHTML = `<video controls><source src="${{fileUrl}}" type="${{mimeType}}"></video>`;
                }} else if (mimeType.startsWith('audio/')) {{
                    previewContent.innerHTML = `<audio controls><source src="${{fileUrl}}" type="${{mimeType}}"></audio>`;
                }} else if (mimeType === 'application/pdf') {{
                    previewContent.innerHTML = `<iframe src="${{fileUrl}}"></iframe>`;
                }} else if (mimeType.startsWith('text/')) {{
                    const response = await fetch(fileUrl);
                    const text = await response.text();
                    previewContent.innerHTML = `<pre>${{escapeHtml(text.substring(0, 10000))}}</pre>`;
                }}
                
                previewContainer.classList.add('show');
                submitBtn.textContent = '✅ 验证成功';
            }} catch (error) {{
                alert('预览加载失败：' + error.message);
                submitBtn.disabled = false;
                submitBtn.textContent = '🔍 重试';
            }}
        }}
        
        function downloadFile() {{
            if (!downloadKey) {{
                alert('请先输入密钥');
                return;
            }}
            
            window.location.href = `file?key=${{encodeURIComponent(downloadKey)}}`;
            
            setTimeout(() => {{
                alert('下载已开始！\\n\\n此下载链接已失效。');
            }}, 1000);
        }}
        
        function escapeHtml(text) {{
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }}
    </script>
</body>
</html>"""


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
    
    def _send_html_error(self, code: int, message: str):
        """Send HTML error page."""
        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>错误 - SSHChat</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }}
        .container {{
            background: white;
            border-radius: 16px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            padding: 60px 40px;
            max-width: 500px;
            text-align: center;
        }}
        h1 {{
            font-size: 72px;
            margin-bottom: 20px;
        }}
        h2 {{
            color: #f5576c;
            margin-bottom: 15px;
            font-size: 24px;
        }}
        p {{
            color: #666;
            font-size: 16px;
            line-height: 1.6;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>❌</h1>
        <h2>错误 {code}</h2>
        <p>{message}</p>
    </div>
</body>
</html>"""
        self.send_response(code)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(html.encode('utf-8'))))
        self.end_headers()
        self.wfile.write(html.encode('utf-8'))
    
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
        
        if len(path_parts) < 2 or path_parts[0] != 'upload':
            self._send_error_json(404, "Not found")
            return
        
        token = path_parts[1]
        query_params = parse_qs(parsed.query)
        key = query_params.get('key', [''])[0]
        
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
        """Handle file download page or direct file download."""
        parsed = urlparse(self.path)
        path_parts = parsed.path.strip('/').split('/')
        
        if len(path_parts) < 2:
            self._send_error_json(404, "Not found")
            return
        
        if path_parts[0] == 'upload':
            # Show upload page
            token = path_parts[1]
            store = file_sharing.file_transfer_store
            transfer = store.get_transfer_by_token(token)
            
            if not transfer or transfer.upload_token != token:
                self._send_html_error(404, "Invalid upload link")
                return
            
            if transfer.upload_used:
                self._send_html_error(403, "Upload link already used")
                return
            
            if time.time() > transfer.upload_expires:
                self._send_html_error(403, "Upload link expired")
                return
            
            # Send upload page
            html = generate_upload_page(token)
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(html.encode('utf-8'))))
            self.end_headers()
            self.wfile.write(html.encode('utf-8'))
            return
        
        if path_parts[0] == 'download':
            token = path_parts[1]
            store = file_sharing.file_transfer_store
            
            # Check if this is a file download request
            if len(path_parts) == 3 and path_parts[2] == 'file':
                # Direct file download/preview
                query_params = parse_qs(parsed.query)
                key = query_params.get('key', [''])[0]
                
                if not key:
                    self._send_error_json(400, "Missing key parameter")
                    return
                
                # Validate token and key
                valid, transfer, error = store.validate_download(token, key)
                
                if not valid:
                    self._send_error_json(403, error)
                    return
                
                if not transfer.file_path or not os.path.exists(transfer.file_path):
                    self._send_error_json(404, "File not found")
                    return
                
                try:
                    # Determine MIME type
                    mime_type = get_mime_type(transfer.filename)
                    
                    # Send file
                    self.send_response(200)
                    self.send_header('Content-Type', mime_type)
                    
                    # For previewable types, use inline disposition
                    if is_previewable(mime_type):
                        self.send_header('Content-Disposition', 
                                       f'inline; filename="{quote(transfer.filename)}"')
                    else:
                        self.send_header('Content-Disposition', 
                                       f'attachment; filename="{quote(transfer.filename)}"')
                    
                    self.send_header('Content-Length', str(transfer.file_size))
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.send_header('Cache-Control', 'no-store')
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
                
            else:
                # Show download page
                transfer = store.get_transfer_by_token(token)
                
                if not transfer:
                    self._send_html_error(404, "Invalid download link")
                    return
                
                if not transfer.upload_used:
                    self._send_html_error(403, "File not yet uploaded")
                    return
                
                if time.time() > transfer.download_expires:
                    self._send_html_error(403, "Download link expired")
                    return
                
                # Check if already downloaded
                if transfer.download_used.get(token, False):
                    self._send_html_error(403, "Download link already used")
                    return
                
                # Send download page
                mime_type = get_mime_type(transfer.filename)
                html = generate_download_page(token, transfer.filename, 
                                             transfer.file_size, mime_type)
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(html.encode('utf-8'))))
                self.end_headers()
                self.wfile.write(html.encode('utf-8'))
            return
        
        self._send_error_json(404, "Not found")


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
