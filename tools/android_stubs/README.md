Minimal signature stubs of the Android/org.json APIs used by
`android/src/org/olares/tuios/EinkBrowser.java`, so CI (and `tools/check_java.py`)
can type-check the WebView helper in seconds without the Android SDK. They
mirror the real signatures; they are never packaged into the APK.
