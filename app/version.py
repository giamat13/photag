"""The app version: the single source of truth.

build.bat reads it for the installer (installer.iss /DMyAppVersion), and the update check compares it with the
tag of the latest GitHub release (tag "v1.2.3" or "1.2.3"). Bump it before every release.

Numbering (from the release after 1.8.1): "FEATURE.FIX", written without the old leading "1." -- 1.8.1 became "8.1".
A release with a new feature raises the first number and resets the second ("9.0"); a release with only fixes raises the
second ("8.2"). Pre-releases add "-beta.N" ("9.0-beta.1"). "9.0" and "9.0.0" are the same version everywhere.
The FIRST release in this scheme is written with three parts ("9.0.0" / "8.2.0"): programs that are already installed
only recognise a code update whose version has three parts. From the one after it on, use the short form.
"""
__version__ = "1.8.1"
REPO = "giamat13/photag"
