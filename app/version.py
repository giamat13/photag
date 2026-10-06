"""The app version: the single source of truth.

build.bat reads it for the installer (installer.iss /DMyAppVersion), and the update check compares it with the
tag of the latest GitHub release (tag "v1.2.3" or "1.2.3"). Bump it before every release.

Numbering (from the release after 1.8.1): "FEATURE.FIX", written without the old leading "1." -- 1.8.1 became "8.1".
A release with a new feature raises the first number and resets the second ("9.0.0"); a release with only fixes raises
the second ("9.1.0"). Pre-releases add "-beta.N" ("9.1.0-beta.1"). ALWAYS write all three parts, the last one 0:
programs installed before this scheme only recognise a code update "photag-code-X.Y.Z-rtN.zip" with three parts.
(The short "9.1" is still read as 9.1.0 everywhere, but is never written.)
"""
__version__ = "10.0.0"
REPO = "giamat13/photag"
