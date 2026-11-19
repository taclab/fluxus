# Mobile

## Installable web app (PWA)

Served over HTTPS, the web interface installs as an app: *Install app* in Chrome or Edge (desktop,
Android), *Share → Add to Home Screen* in Safari (iOS). It then opens full screen, with the same
offline mode as in the browser.

- **Share → Fluxus** (Android, desktop Chrome or Edge): from a page opened elsewhere, opens the
  add-feed dialog with the URL prefilled.
- **Shortcuts**: a long press on the icon offers *Non lus* and *Favoris*.
- **Updates**: when a new version is deployed, a banner offers to reload; nothing reloads mid-reading.
- **Persistent storage**: after login, Fluxus asks the browser not to evict the local database when
  space runs low.

## Android app

Download `fluxus-X.Y.Z.apk` from the GitHub release, install it, then enter the server's HTTPS
address and the password. The app gets a long-lived **device token**, revocable in
*Paramètres → Appareils*. The hardware back button navigates inside the app. Sync happens on launch
and when the app returns to the foreground; there is no scheduled sync while it is closed.

If a debug APK is installed, uninstall it once before installing a release APK: they are signed with
different keys (data lives on the server, just log in again).

### Building locally

The front end is packaged with Capacitor. No Android SDK is needed on the machine; the build runs in
a container:

```bash
scripts/build-apk.sh            # debug   -> dist/app-debug.apk (installable as is)
scripts/build-apk.sh release    # release -> dist/app-release.apk, signed if a key is provided
```

The first run builds the image (JDK + Android SDK, a few minutes); later runs take one or two
minutes thanks to the Gradle cache. The APK version is read from `web/package.json`.

### Signed APK in every release

On each `vX.Y.Z` tag, CI builds the APK, signs it and attaches it to the tag's GitHub release
(`fluxus-X.Y.Z.apk`), with notes from `CHANGELOG.md`. This needs a **signing key**, created once.
Android only installs an update signed with the same key as the installed version: keep the file
and its password outside the repository, somewhere safe. Losing them means uninstalling the app on
every device.

```bash
# 1. Create the key (keytool ships in the build image; it asks for a password)
docker run --rm -it -v "$PWD:/k" -w /k fluxus-android-builder \
  keytool -genkeypair -keystore fluxus-release.jks -alias fluxus -keyalg RSA -keysize 4096 -validity 10000 -dname "CN=Fluxus"
sudo chown "$USER" fluxus-release.jks && mv fluxus-release.jks ~/   # outside the repository

# 2. Check the password
docker run --rm -it -v "$HOME:/k" fluxus-android-builder keytool -list -keystore /k/fluxus-release.jks

# 3. Encode it for GitHub (copy from the file, not from the terminal: the line is long)
base64 -w0 ~/fluxus-release.jks > ~/fluxus-release.b64
```

In the GitHub repository: *Settings → Secrets and variables → Actions → Secrets →
**New repository secret*** (repository secrets, not environment secrets nor variables):

| Secret | Value |
|---|---|
| `ANDROID_KEYSTORE_BASE64` | content of `fluxus-release.b64` (delete the file afterwards) |
| `ANDROID_KEYSTORE_PASSWORD` | the password from step 1 |
| `ANDROID_KEY_ALIAS` | `fluxus` |
| `ANDROID_KEY_PASSWORD` | optional, defaults to the keystore password |

Without these secrets the release is created without an APK. To add it afterwards: *Actions* →
the tag's « Publication (images, APK, release) » run → *Re-run jobs*.
« keystore password was incorrect » means a wrong password or a truncated base64 value.

The same key works locally:
`FLUXUS_KEYSTORE_FILE=~/fluxus-release.jks FLUXUS_KEYSTORE_PASSWORD=… FLUXUS_KEY_ALIAS=fluxus scripts/build-apk.sh release`.
