# Deploy SSHChat on iSH (server)

Run the chat **server** inside iSH (Alpine Linux on iPhone/iPad). For using iSH only as an SSH **client**, see [ish-beginner.md](ish-beginner.md).

Chinese original: [DEPLOY-iSH.md](../../DEPLOY-iSH.md)

## Overview

`deploy.sh` detects `/ish`, disables Cloudflare by default, uses a slimmer dependency set, and can install an OpenRC keep-alive service.

## Checklist

1. Install iSH; give it enough storage; keep the device awake/charging for a long-running server.
2. Copy or clone the SSHChat tree onto the device.
3. Run:

```bash
chmod +x deploy.sh
./deploy.sh
```

4. Note the listen port (`SSHCHAT_PORT`, default `12345`) and how clients reach the phone (LAN IP, reverse SSH, or a tunnel).
5. Create Linux users / SSH keys with the project admin scripts (`admin-add-user.sh`, etc.) as documented in [README.zh.md](../../README.zh.md).

## Tips

- Cellular IPs change; prefer Wi‑Fi + port forward, or an outbound tunnel.
- For file `/sendfile` HTTPS on the public Internet, a tunnel (Cloudflare or similar) is usually required; on pure LAN, HTTP may suffice. The shared drawing board (`/canvas`) uses the same file HTTP port.
- Set `SSHCHAT_DEFAULT_LOCALE=en` (default) or `zh` for the whole server default language.

## More

Full Alpine/OpenRC details, pitfalls, and Chinese screenshots: [DEPLOY-iSH.md](../../DEPLOY-iSH.md).
