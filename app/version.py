"""The app version: the single source of truth.

build.bat reads it for the installer (installer.iss /DMyAppVersion), and the update check compares it with the
tag of the latest GitHub release (tag "v1.2.3" or "1.2.3"). Bump it before every release.

Numbering "FEATURE.SMALL-FEATURE.FIX" (decided with the user; 1.8.1 became "8.1.0", a third part was added from 11.0.0, and from
18.1.0 the second number is a small feature and the third a fix): a release with a new feature raises the first number and
resets the others ("12.0.0"); a release with only small features raises the second and resets the third ("12.1.0"); a release
with only fixes raises the third ("12.1.1").
Pre-releases add "-beta.N" ("11.1.0-beta.1"). ALWAYS write all three parts: programs installed before this scheme only
recognise a code update "photag-code-X.Y.Z-rtN.zip" with three parts. (The short "9.1" is still read as 9.1.0.)
"""
__version__ = "18.0.0"
REPO = "giamat13/photag"
