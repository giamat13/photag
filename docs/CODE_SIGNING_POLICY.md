# Code signing policy

photag's Windows programs (`photag.exe`, `photag-backup.exe` and the installer `photagSetup.exe`) are signed so that Windows can tell they
come from this project and were not changed afterwards (this is what Windows Smart App Control and SmartScreen look for).

Free code signing provided by [SignPath.io](https://signpath.io), certificate by [SignPath Foundation](https://signpath.org).

## What is signed
Only files built by this repository's own GitHub Actions workflow ([`release.yml`](../.github/workflows/release.yml)) from the public source
code in <https://github.com/giamat13/photag>, in this order: `photag.exe` and `photag-backup.exe` first, then the installer that contains
them. Nothing else is signed: no third-party binaries are submitted, and the signing request is accepted only when it comes from that workflow
run (SignPath checks the origin of the build). The workflow stops, and no release is published, if any of the three files comes back
without a valid signature.

## Roles
| Role | Who |
|---|---|
| Author / committer | the project owner ([@giamat13](https://github.com/giamat13)) and contributors whose pull requests the owner merges |
| Reviewer | the project owner reviews every change that is merged |
| Approver (approves each signing request) | the project owner ([@giamat13](https://github.com/giamat13)) |

## Privacy
photag works on your computer. It does not send your photos, catalog or any other personal information anywhere on its own. It contacts the
internet only when you ask it to or when you switched a feature on:
- the update check (once a day, can be turned off in Preferences) asks GitHub for the latest release of this project;
- AI tagging sends small thumbnails to the AI provider you chose, only when you start it;
- the triplan connection talks to your own triplan account, only when you connect it.

The full privacy policy is [docs/PRIVACY.md](PRIVACY.md).

## Reporting a problem
Open an issue at <https://github.com/giamat13/photag/issues>.

## For the maintainer: how to switch signing on
Signing is built into the release workflow but stays OFF until these exist (so a normal build is unchanged without them):

1. **Apply** for the free certificate at <https://signpath.org/apply> (open-source projects; this repository is public and GPL-3.0). Link this
   page when asked for the code signing policy.
2. In SignPath, once approved: create a project (its *slug* is used below), connect it to GitHub (the SignPath "GitHub Actions" trusted build
   system), and add two **artifact configurations** and one **signing policy**:
   - artifact configuration `programs`
     ```xml
     <artifact-configuration xmlns="http://signpath.io/artifact-configuration/v1">
       <zip-file>
         <pe-file path="photag.exe"><authenticode-sign/></pe-file>
         <pe-file path="photag-backup.exe"><authenticode-sign/></pe-file>
       </zip-file>
     </artifact-configuration>
     ```
   - artifact configuration `installer`
     ```xml
     <artifact-configuration xmlns="http://signpath.io/artifact-configuration/v1">
       <zip-file>
         <pe-file path="photagSetup.exe"><authenticode-sign/></pe-file>
       </zip-file>
     </artifact-configuration>
     ```
   - signing policy `release-signing` (or any slug, then set `SIGNPATH_SIGNING_POLICY_SLUG`).
3. In the GitHub repository, *Settings → Secrets and variables → Actions*:
   - **Variables:** `SIGNPATH_ORGANIZATION_ID`, `SIGNPATH_PROJECT_SLUG` (and optionally `SIGNPATH_SIGNING_POLICY_SLUG`)
   - **Secret:** `SIGNPATH_API_TOKEN` (an API token of a SignPath user that may submit signing requests)
4. Run the release workflow as usual. It signs, checks every signature (`tools/signing.py`) and only then publishes.

Not part of this setup: the in-app code update (`photag-code-*.zip`) contains no program, only Python source, so there is nothing to sign.
