#!/usr/bin/env python3
"""End-to-end check of the upload/preview/download HTTP flow and its replay defences."""

import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.request
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

tmpdir = tempfile.mkdtemp(prefix="sshchat_e2e_")
os.environ["SSHCHAT_FILE_STORAGE_DIR"] = os.path.join(tmpdir, "files")
os.environ["SSHCHAT_FILE_TRANSFER_STORE"] = os.path.join(tmpdir, "transfers.json")

import file_sharing
import file_http_server

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6360000002000100ffff0300000600"
    "0557bfabd40000000049454e44ae426082"
)

server = file_http_server.FileHTTPServer(host="127.0.0.1", port=18443, use_https=False)
server.start()
base = "http://127.0.0.1:18443"


def request(url, method="GET", data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def multipart(filename, content):
    boundary = uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode("utf-8") + content + f"\r\n--{boundary}--\r\n".encode("utf-8")
    return body, {"Content-Type": f"multipart/form-data; boundary={boundary}"}


store = file_sharing.file_transfer_store
transfer = store.create_upload_session(sender="alice", recipients=["bob"])
upload_url = f"{base}/upload/{transfer.upload_token}"

print("== 上传 ==")

status, _, body = request(upload_url)
assert status == 200, status
page = body.decode("utf-8")
assert "安全文件上传" in page
assert transfer.upload_key not in page, "upload key leaked into the page"
# The page must not build any URL that carries the key
assert "?key=" not in page and "key=' + " not in page, "upload page still puts key in a URL"
print(f"1. 上传页 {status}，页面内不含密钥、也不拼 key 到网址")

payload, hdrs = multipart("我的截图.png", PNG)
status, _, body = request(upload_url, "POST", payload, {**hdrs, "X-Upload-Key": "WRONG1"})
assert status == 403, status
print(f"2. 错误密钥被拒（密钥走请求头）: {status} {body.decode('utf-8')}")

payload, hdrs = multipart("我的截图.png", PNG)
status, _, body = request(upload_url, "POST", payload, {**hdrs, "X-Upload-Key": transfer.upload_key})
assert status == 200, (status, body)
print(f"3. 上传成功，文件名取自上传文件: {store.get_transfer_by_token(transfer.upload_token).filename}")

payload, hdrs = multipart("again.png", PNG)
status, _, _ = request(upload_url, "POST", payload, {**hdrs, "X-Upload-Key": transfer.upload_key})
assert status == 403, status
print(f"4. 上传网址一次性，重放被拒: {status}")

print("\n== 下载页与取件 ==")

token = transfer.download_tokens["bob"]
key = transfer.download_keys["bob"]
page_url = f"{base}/download/{token}"

status, _, body = request(page_url)
assert status == 200, status
page = body.decode("utf-8")
assert "我的截图.png" in page
assert key not in page, "download key leaked into the page"
assert "?key=" not in page, "download page still puts key in a URL"
assert "/download/%s/file" % token not in page, "old keyed file endpoint is back"
m = re.search(r'const ticketEndpoint = "([^"]+)"', page)
assert m and m.group(1) == f"/download/{token}/ticket", m
print(f"5. 下载页 {status}，密钥不在页面也不在任何网址里")

ticket_url = f"{base}/download/{token}/ticket"
hdrs = {"Content-Type": "application/json"}
status, _, body = request(ticket_url, "POST", json.dumps({"key": "WRONG2"}).encode(), hdrs)
assert status == 403, status
print(f"6. 密钥错误拿不到凭据: {status} {body.decode('utf-8')}")

status, _, body = request(ticket_url, "POST", json.dumps({"key": key}).encode(), hdrs)
assert status == 200, status
tickets = json.loads(body)
preview_url, download_url = tickets["preview"], tickets["download"]
assert preview_url and download_url and preview_url != download_url
assert "key" not in preview_url and "key" not in download_url
print(f"7. 密钥正确换到两条独立凭据链接，且互不相同")
print(f"   预览: {preview_url}")
print(f"   下载: {download_url}")

print("\n== 重放防护 ==")

status, hdrs_p, body = request(base + preview_url)
assert status == 200 and body == PNG, status
assert hdrs_p["Content-Disposition"].startswith("inline")
print(f"8. 预览链接首次可用: {status}，inline，字节与原文件一致")

status, _, body = request(base + preview_url)
assert status == 403, f"预览链接竟然可以重放: {status}"
print(f"9. 预览链接重放被拒: {status} {body.decode('utf-8')}")

# A captured preview link must not work as a download link either
status, _, _ = request(base + download_url.replace(download_url, preview_url))
assert status == 403
print("10. 已用过的凭据换个用途也无效")

status, hdrs_d, body = request(base + download_url)
assert status == 200 and body == PNG, status
assert hdrs_d["Content-Disposition"].startswith("attachment")
print(f"11. 下载链接首次可用: {status}，attachment")

status, _, body = request(base + download_url)
assert status == 403, f"下载链接竟然可以重放: {status}"
print(f"12. 下载链接重放被拒: {status} {body.decode('utf-8')}")

status, _, body = request(page_url)
assert status == 403, f"下载完成后页面仍可打开: {status}"
assert "已使用过" in body.decode("utf-8")
print(f"13. 下载完成后，整个下载页也作废: {status}")

status, _, _ = request(ticket_url, "POST", json.dumps({"key": key}).encode(),
                       {"Content-Type": "application/json"})
assert status == 403, f"用过的下载链接还能换凭据: {status}"
print(f"14. 用过的下载链接换不到新凭据: {status}")

print("\n== 断线可重试 ==")

t2 = store.create_upload_session(sender="alice", recipients=["carol"])
payload, hdrs = multipart("doc.txt", b"hello world")
request(f"{base}/upload/{t2.upload_token}", "POST", payload,
        {**hdrs, "X-Upload-Key": t2.upload_key})
token2 = t2.download_tokens["carol"]
key2 = t2.download_keys["carol"]
jhdr = {"Content-Type": "application/json"}

status, _, body = request(f"{base}/download/{token2}/ticket", "POST",
                          json.dumps({"key": key2}).encode(), jhdr)
first = json.loads(body)
# Pretend the preview was fetched but the download never happened
request(base + first["preview"])
status, _, body = request(f"{base}/download/{token2}/ticket", "POST",
                          json.dumps({"key": key2}).encode(), jhdr)
assert status == 200, status
second = json.loads(body)
assert second["download"] != first["download"], "re-entering the key must mint fresh tickets"
print("15. 没下载完时重新输密钥可换一组全新凭据")

status, _, _ = request(base + first["download"])
assert status == 403, "旧凭据在换发后仍然有效"
print("16. 换发后旧凭据立即失效，避免旧链接被留存复用")

status, _, _ = request(base + second["download"])
assert status == 200
print("17. 新凭据可正常下载")

server.stop()
print("\n✅ 端到端流程与重放防护全部通过")
