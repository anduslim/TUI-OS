package android.view; import android.content.Context;
public abstract class ViewGroup extends View implements ViewParent { public ViewGroup(Context c){super(c);} public void removeView(View v){}
 public static class LayoutParams { public int width, height; public LayoutParams(int w, int h){} }
 public static class MarginLayoutParams extends LayoutParams { public int leftMargin, topMargin; public MarginLayoutParams(int w,int h){super(w,h);} } }
