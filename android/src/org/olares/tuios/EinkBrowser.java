package org.olares.tuios;

import android.app.Activity;
import android.graphics.Color;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;

import org.json.JSONObject;

/**
 * A native WebView laid over the TUI's Browser tab.
 *
 * Python (pyjnius) owns the policy: where the view sits (pixel rect from the
 * 147x46 cell grid), what to load, and the E-ink user style. This class does
 * the Android work. Every public method is safe to call from any thread;
 * the work is posted to the UI thread.
 *
 * Keyboard: while the page has focus the hardware keyboard drives it
 * natively. A few keys are handed back to the TUI via Listener#onKey:
 *   Esc          release the page (focus returns to the TUI)
 *   F1-F5        switch tab (released first)
 *   Ctrl+L       focus the TUI's location bar (released first)
 *   Ctrl+B / Q   telemetry toggle / quit
 *   BACK         page history, then release when there's no history left
 */
public class EinkBrowser {

    public interface Listener {
        /** kind: started | finished | error | navigate (about: links). */
        void onPageEvent(String kind, String url, String title);

        /** A key the TUI handles: escape, f1..f6, ctrl+1..6, ctrl+b, ctrl+l, ctrl+q. */
        void onKey(String key);
    }

    private final Activity activity;
    private final Listener listener;
    private WebView web;
    private String userCss = "";
    private String userJs = "";

    public EinkBrowser(Activity activity, Listener listener) {
        this.activity = activity;
        this.listener = listener;
    }

    private void ui(Runnable r) {
        activity.runOnUiThread(r);
    }

    private void ensure() {
        if (web != null) {
            return;
        }
        web = new WebView(activity);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setUseWideViewPort(true);
        s.setLoadWithOverviewMode(true);
        s.setBuiltInZoomControls(false);
        s.setMediaPlaybackRequiresUserGesture(true);
        web.setBackgroundColor(Color.BLACK);
        web.setOverScrollMode(View.OVER_SCROLL_NEVER);
        web.setFocusable(true);
        web.setFocusableInTouchMode(true);

        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                String url = request.getUrl().toString();
                if (url.startsWith("about:")) {
                    listener.onPageEvent("navigate", url, "");
                    return true;
                }
                // Stay in the WebView for web pages; ignore intent:, mailto:, etc.
                return !(url.startsWith("http://") || url.startsWith("https://"));
            }

            @Override
            public void onPageStarted(WebView view, String url, android.graphics.Bitmap favicon) {
                listener.onPageEvent("started", url, "");
            }

            @Override
            public void onPageCommitVisible(WebView view, String url) {
                inject();
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                inject();
                String title = view.getTitle();
                listener.onPageEvent("finished", url, title == null ? "" : title);
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) {
                    listener.onPageEvent("error", request.getUrl().toString(),
                            String.valueOf(error.getDescription()));
                }
            }
        });

        web.setOnKeyListener(new View.OnKeyListener() {
            @Override
            public boolean onKey(View v, int keyCode, KeyEvent event) {
                String key = tuiKey(keyCode, event);
                if (key == null) {
                    return false;
                }
                if (event.getAction() != KeyEvent.ACTION_DOWN) {
                    return true; // swallow the matching key-up too
                }
                if (key.equals("back")) {
                    if (web.canGoBack()) {
                        web.goBack();
                        return true;
                    }
                    key = "escape";
                }
                if (!key.equals("ctrl+b")) {
                    web.clearFocus(); // focus falls back to the SDL surface (the TUI)
                }
                listener.onKey(key);
                return true;
            }
        });

        FrameLayout.LayoutParams lp = new FrameLayout.LayoutParams(1, 1);
        lp.gravity = Gravity.TOP | Gravity.START;
        activity.addContentView(web, lp);
        web.setVisibility(View.GONE);
    }

    static String tuiKey(int keyCode, KeyEvent event) {
        switch (keyCode) {
            case KeyEvent.KEYCODE_ESCAPE: return "escape";
            case KeyEvent.KEYCODE_BACK: return "back";
            case KeyEvent.KEYCODE_F1: return "f1";
            case KeyEvent.KEYCODE_F2: return "f2";
            case KeyEvent.KEYCODE_F3: return "f3";
            case KeyEvent.KEYCODE_F4: return "f4";
            case KeyEvent.KEYCODE_F5: return "f5";
            case KeyEvent.KEYCODE_F6: return "f6";
            default: break;
        }
        if (event.isCtrlPressed()) {
            switch (keyCode) {
                case KeyEvent.KEYCODE_B: return "ctrl+b";
                case KeyEvent.KEYCODE_L: return "ctrl+l";
                case KeyEvent.KEYCODE_Q: return "ctrl+q";
                case KeyEvent.KEYCODE_1: return "ctrl+1";
                case KeyEvent.KEYCODE_2: return "ctrl+2";
                case KeyEvent.KEYCODE_3: return "ctrl+3";
                case KeyEvent.KEYCODE_4: return "ctrl+4";
                case KeyEvent.KEYCODE_5: return "ctrl+5";
                case KeyEvent.KEYCODE_6: return "ctrl+6";
                default: break;
            }
        }
        return null;
    }

    private void inject() {
        if (web == null) {
            return;
        }
        String js = "(function(){var id='tuios-eink';var s=document.getElementById(id);"
                + "if(!s){s=document.createElement('style');s.id=id;"
                + "(document.head||document.documentElement).appendChild(s);}"
                + "s.textContent=" + JSONObject.quote(userCss) + ";})();" + userJs;
        web.evaluateJavascript(js, null);
    }

    // ------------------------------------------------------------ public API

    public void setUserStyle(final String css, final String js) {
        ui(new Runnable() {
            @Override
            public void run() {
                userCss = css == null ? "" : css;
                userJs = js == null ? "" : js;
                inject();
            }
        });
    }

    /** Place the view at a pixel rect (window coordinates, top-left origin). */
    public void show(final int x, final int y, final int width, final int height) {
        ui(new Runnable() {
            @Override
            public void run() {
                ensure();
                ViewGroup.LayoutParams base = web.getLayoutParams();
                FrameLayout.LayoutParams lp = base instanceof FrameLayout.LayoutParams
                        ? (FrameLayout.LayoutParams) base
                        : new FrameLayout.LayoutParams(width, height);
                lp.width = Math.max(1, width);
                lp.height = Math.max(1, height);
                lp.leftMargin = x;
                lp.topMargin = y;
                lp.gravity = Gravity.TOP | Gravity.START;
                web.setLayoutParams(lp);
                web.setVisibility(View.VISIBLE);
            }
        });
    }

    public void hide() {
        ui(new Runnable() {
            @Override
            public void run() {
                if (web != null) {
                    web.clearFocus();
                    web.setVisibility(View.GONE);
                }
            }
        });
    }

    public void load(final String url) {
        ui(new Runnable() {
            @Override
            public void run() {
                ensure();
                web.loadUrl(url);
            }
        });
    }

    public void loadHtml(final String html, final String baseUrl) {
        ui(new Runnable() {
            @Override
            public void run() {
                ensure();
                web.loadDataWithBaseURL(baseUrl, html, "text/html", "utf-8", baseUrl);
            }
        });
    }

    public void back() {
        ui(new Runnable() {
            @Override
            public void run() {
                if (web != null && web.canGoBack()) {
                    web.goBack();
                }
            }
        });
    }

    public void reload() {
        ui(new Runnable() {
            @Override
            public void run() {
                if (web != null) {
                    web.reload();
                }
            }
        });
    }

    public void focus() {
        ui(new Runnable() {
            @Override
            public void run() {
                if (web != null && web.getVisibility() == View.VISIBLE) {
                    web.requestFocus();
                }
            }
        });
    }

    public void destroy() {
        ui(new Runnable() {
            @Override
            public void run() {
                if (web != null) {
                    ViewGroup parent = (ViewGroup) web.getParent();
                    if (parent != null) {
                        parent.removeView(web);
                    }
                    web.destroy();
                    web = null;
                }
            }
        });
    }
}
