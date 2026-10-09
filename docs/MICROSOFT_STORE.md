# Publishing photag in the Microsoft Store

**Why:** Windows 11 *Smart App Control* blocks every program that is not signed or known to Microsoft, and photag is not signed.
Tested in Windows Sandbox: no script, no self-made certificate and no signed Python gets `photag.exe` (or its Pillow / numpy / pydantic
modules) past it. A Store app is signed by Microsoft when it is published, so it runs there. Registering as an individual developer is
**free** since September 2025 (no credit card), and a rejected submission can be fixed and sent again at no cost.

## What you do (once)
1. Go to <https://storedeveloper.microsoft.com>, sign in with a Microsoft account and register as an **individual** (a short ID check).
2. In Partner Center: **Apps and games > New product > MSIX or PWA app**, reserve the name **photag** (or your variant if it is taken).
3. Open the product: **Product management > Product identity**. Copy three values:
   * *Package/Identity/Name*  (looks like `12345YourName.photag`)
   * *Package/Identity/Publisher*  (looks like `CN=XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX`)
   * *Package/Properties/PublisherDisplayName*  (your publisher name)
4. In GitHub, repository **Settings > Secrets and variables > Actions > Variables**, add three **repository variables**:
   `STORE_IDENTITY_NAME`, `STORE_PUBLISHER`, `STORE_PUBLISHER_NAME` with those values.

## What the release does
With those variables set, the *release* workflow also builds `photag-<version>.msix` and keeps it as the workflow artifact
**photag-store-msix** (30 days). Without them nothing changes. The MSIX is **not** attached to the public GitHub release.

## What you do for each Store release
1. Download the `photag-store-msix` artifact of the release run and unzip it.
2. Partner Center > the product > **Start your submission**: upload the `.msix`, fill in the listing once (description, screenshots,
   age rating, privacy policy URL: `docs/PRIVACY.md` on GitHub), answer the questionnaire.
3. The app uses the restricted capability **runFullTrust** (it is a normal desktop program, not a sandboxed one). The submission asks why:
   *"photag is a desktop photo manager written in Python and packaged with PyInstaller; it needs full trust to read and write the user's photo folders, run a local server and use ffmpeg."*
4. Submit. Certification can take from hours to days. If it is rejected, read the reason, fix, and resubmit (free).

## How a Store copy differs
* The Store updates it: the in-app updater does no check, and the scheduled background backup task is not registered (the app's own
  catch-up backups still run while it is open). See `app/config.py: IN_STORE_PACKAGE`.
* Photos and the catalog stay in your own folder (`~/Photag` by default) and are not touched by uninstalling. The small settings pointer
  lives in the package's private AppData and goes with an uninstall (the library is found again from the default folder).

## Not done / unknown
* Nobody has submitted this package yet: whether certification passes is unknown.
* The Store build has not been run on a PC with Smart App Control on. Do that with the first Store build.

## Wording of the Store listing (do not skip)
- The name, the short and the long description, the keywords and the screenshots' captions must **not contain "Lightroom"** (or any other
  Adobe name or logo). Describe photag in its own words: "a local photo manager: catalog, collections, ratings, keywords, face
  recognition, non-destructive editing, backups".
- "Import from a Lightroom catalog" may be mentioned once in the long description as a compatibility feature, followed by:
  "Lightroom is a trademark of Adobe; photag is not affiliated with Adobe." Not in the keywords.
- Use photag's own icon and screenshots only.
