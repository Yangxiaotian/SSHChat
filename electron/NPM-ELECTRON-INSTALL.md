# Fixing `npm ci` / Electron postinstall errors

Installing the `electron` package runs `node install.js`, which downloads the platform binary via `@electron/get` and `got`. Failures usually show up as errors under `path .../node_modules/electron`.

## `TypeError: Invalid URL` (got / `@electron/get`)

This almost always means the **download URL is not parseable**. `@electron/get` builds it from npm config and environment variables (see [artifact-utils mirrorVar](https://github.com/electron/get/blob/main/src/artifact-utils.ts)).

**Typical mistakes**

- `electron_mirror` or `ELECTRON_MIRROR` is set **without** `https://` (for example only `npmmirror.com/mirrors/electron/`).
- A stray or copied `.npmrc` sets `electron_mirror` to garbage, empty placeholder, or a non-URL.
- Duplicate or conflicting settings in project `electron/.npmrc`, user `~/.npmrc`, and the shell environment.

**What to do**

1. Inspect project config:

   ```bash
   cd electron
   npm config list
   ```

2. Look for Electron-related entries and inspect any project file (npm log lines like `config load:file:.../electron/.npmrc` mean you have `./electron/.npmrc`):

   ```bash
   cat .npmrc 2>/dev/null || true
   ```

3. Unset broken values, then reinstall:

   ```bash
   npm config delete electron_mirror
   unset ELECTRON_MIRROR ELECTRON_CUSTOM_DIR ELECTRON_CUSTOM_FILENAME
   rm -rf node_modules
   npm ci
   ```

4. If GitHub Releases is slow or blocked, use a **full HTTPS mirror URL** (see `.npmrc.example` in this folder), e.g.:

   ```text
   electron_mirror=https://npmmirror.com/mirrors/electron/
   ```

## Other errors (network, timeout, `ReadError`)

You may see `ReadError`, `ECONNRESET`, TLS errors, or slow hangs. Those are **network / proxy / firewall** issues, not invalid URLs. Retry, fix VPN/proxy, or use a stable mirror as above.

## Deprecated package warnings

Warnings from `rimraf`, `glob`, `npmlog`, etc. come from transitive dependencies; they do not by themselves cause the install to fail.
