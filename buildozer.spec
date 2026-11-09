[app]

# (str) Title of your application
title = Olares Workspace TUI

# (str) Package name
package.name = olares_workspace_tui

# (str) Package domain (needed for android package naming)
package.domain = org.olares

# (str) Source code directory where main.py sits
source.dir = .

# (list) Source files to include (let it scan all python and ini styles)
# TUI-OS: + tcss (Textual stylesheet, the app will not start without it),
#         + toml (bundled android/config.toml), + ttf/txt (JetBrains Mono + OFL licence),
#         + yaml (k9s E-ink skin and config, uploaded to the node per session)
source.include_exts = py,png,jpg,kv,atlas,ini,css,tcss,toml,ttf,txt,yaml

# TUI-OS: keep tests, design scratch and build output out of the APK
source.exclude_dirs = tests, tools, bin, .buildozer, .git, .github, design, __pycache__

# (str) Application version
version = 1.1.1

# (list) Application requirements
# Crucial: include hostpython3, kivy, and external async libraries your code needs
# TUI-OS: "asyncio" is removed on purpose. It is part of CPython's standard
# library; the PyPI package called "asyncio" is an abandoned 2015 backport
# (3.4.3) that does not even byte-compile on Python 3.7+ ("async" became a
# keyword). Pure-Python packages are pip-installed by python-for-android, so
# every transitive dependency is pinned explicitly for a reproducible build.
requirements = hostpython3, python3, kivy,
    textual==8.2.8, rich==15.0.0, markdown-it-py==4.2.0, mdit-py-plugins==0.6.1,
    mdurl==0.1.2, linkify-it-py==2.2.0, uc-micro-py==2.0.0, platformdirs==4.14.5,
    pygments==2.21.0, typing_extensions==4.16.0,
    httpx==0.28.1, httpcore==1.0.9, h11==0.16.0, anyio==4.15.1, sniffio==1.3.1,
    certifi, idna==3.20,
    pyte==0.8.2, wcwidth==0.9.4,
    openssl, cffi, cryptography, asyncssh==2.23.1
# TUI-OS (K9s tab, SSH): openssl/cffi/cryptography are python-for-android
# recipes (cryptography 46.0.3, built with Rust for arm64). asyncssh 2.23.1 is
# the newest release that accepts cryptography 46 (2.24+ needs 48+).

# (str) Supported orientations (Force landscape for the 10.3-inch pogo setup)
orientation = landscape

# (bool) Use fullscreen mode (hides the Android status notification bar)
fullscreen = 1

# TUI-OS: black splash screen so launch doesn't flash the E-ink panel white
android.presplash_color = #000000

# ==============================================================================
# Android specific configuration
# ==============================================================================

# (int) Android API target. Android 11 is API 30.
# TUI-OS: this is the *compile/target* SDK, not the OS the app runs on. The
# SDL2 bootstrap's Java code uses API 31 symbols (BLUETOOTH_CONNECT), so the
# build fails below 31. 33 is python-for-android's tested default. The app
# still installs and runs on the Tab Ultra C's Android 11: that's governed by
# android.minapi below.
android.api = 33

# (int) Minimum API required
android.minapi = 21

# (list) Permissions requested by the app.
# CRUCIAL: INTERNET permission allows your Python script to communicate with the Olares One server.
android.permissions = INTERNET, ACCESS_NETWORK_STATE

# (bool) Enable Android AndroidX support (required for modern Python UI wrappers)
android.enable_androidx = True

# (list) The Android architectures to build for.
# Tab Ultra C uses an octa-core Qualcomm Snapdragon CPU (ARM 64-bit architecture)
android.archs = arm64-v8a

# TUI-OS: relaunching from the launcher returns to the running TUI instead of
# starting a second activity (which would drop the keyboard claim and streams)
android.manifest.launch_mode = singleTask

# TUI-OS: Java helper for the Browser tab's native WebView (EinkBrowser)
android.add_src = android/src

# TUI-OS: lets the WebView open plain-HTTP services on Tailscale hosts
# (MagicDNS names, 100.x addresses). Tailnet traffic is already encrypted
# by WireGuard; most self-hosted dashboards serve HTTP there.
android.extra_manifest_application_arguments = android/manifest_application.xml

# TUI-OS: CI publishes installable APKs (not Play Store AABs) as releases
android.release_artifact = apk
android.debug_artifact = apk

# (str) Android logcat filters
android.logcat_filters = *:S python:D

[buildozer]
# (int) Log level (0 = error only, 1 = info, 2 = debug and stdout)
log_level = 2

# (int) Display warning if buildozer is run as root (0 = false, 1 = true)
warn_on_root = 1
