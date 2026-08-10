# File sharing user guide

Chinese original: [USER_GUIDE_FILE_SHARING.md](../../USER_GUIDE_FILE_SHARING.md)

## After login

You should see `/sendfile` in the tip line. Full details: `/help`.

## Three ways to send

| Goal | Command |
|------|---------|
| Current room | `/sendfile` |
| One user | `/sendfile bob` |
| Specific room | `/sendfile #lobby` (you must be in that room) |

You receive an **upload URL** and a **secret key** on separate lines. Open the URL in a browser, enter the key, choose a file, upload.

Recipients get a **download URL** + key. Keys are **not** in the URL. Upload and download tokens are **single-use**.

## Preview

After entering the download key, the page can preview images, video, audio, PDF, and many text types, then save if you want.

## Offline recipients

`/sendfile <nick>` works when the peer is offline: they get a leave-message with the download link when they next connect. Use `/leave` to list or recall unread leave-messages/files you sent.

## Language of the web pages

Pages default to **English**. Append `?lang=zh` for Chinese UI, e.g. `https://host/u/TOKEN?lang=zh`.

## Safety notes

- Do not paste the key into the address bar or share screenshots that include both URL and key.
- Links expire (upload ~60 minutes, download ~24 hours, one-shot links shorter—see admin config).
- Stolen used links cannot be replayed.

## Troubleshooting

| Symptom | Try |
|---------|-----|
| Page won't open | Check firewall / Cloudflare tunnel; ask admin for the public file URL base |
| Wrong key | Copy carefully; keys are case-sensitive |
| Upload failed | File too large or token expired—request a new `/sendfile` |
| Recipient never got it | Confirm nick spelling; check `/leave` |
