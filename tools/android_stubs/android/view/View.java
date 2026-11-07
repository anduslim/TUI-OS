package android.view; import android.content.Context;
public class View { public static final int VISIBLE=0, GONE=8, OVER_SCROLL_NEVER=2;
 public interface OnKeyListener { boolean onKey(View v, int keyCode, KeyEvent event); }
 public View(Context c){} public void setFocusable(boolean b){} public void setFocusableInTouchMode(boolean b){} public void clearFocus(){} public boolean requestFocus(){return true;}
 public void setVisibility(int v){} public int getVisibility(){return 0;} public ViewGroup.LayoutParams getLayoutParams(){return null;} public void setLayoutParams(ViewGroup.LayoutParams p){}
 public ViewParent getParent(){return null;} public void setBackgroundColor(int c){} public void setOverScrollMode(int m){} public void setOnKeyListener(OnKeyListener l){} }
