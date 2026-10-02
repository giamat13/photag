"""The app version: the single source of truth.

build.bat reads it for the installer (installer.iss /DMyAppVersion), and the update check compares it with the
tag of the latest GitHub release (tag "v1.2.3" or "1.2.3"). Bump it before every release.
"""
__version__ = "1.5.1"
REPO = "giamat13/photag"
