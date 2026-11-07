package android.webkit; import android.content.Context;
public class WebView extends android.widget.FrameLayout { public WebView(Context c){super(c);} public WebSettings getSettings(){return null;} public void setWebViewClient(WebViewClient c){} public String getTitle(){return null;}
 public void evaluateJavascript(String s, ValueCallback<String> cb){} public void loadUrl(String u){} public void loadDataWithBaseURL(String b,String d,String m,String e,String h){} public boolean canGoBack(){return false;} public void goBack(){} public void reload(){} public void destroy(){} }
