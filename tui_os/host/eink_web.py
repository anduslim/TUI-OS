"""User style injected into every page the Android WebView shows.

Pages are re-coloured to the TUI palette: black surfaces, white text, green
links and focus rings, blue selection fill with white text, and yellow marks
with black text. Shadows, gradients, transitions and smooth scrolling are
removed. They render as grey dither and ghosting on E-ink.

Images and video keep their pixels; only the chrome around them changes.
"""

from __future__ import annotations

EINK_CSS = """
:root { color-scheme: dark !important; }
html, body { background: #000000 !important; color: #FFFFFF !important; }
*, *::before, *::after {
  background-color: #000000 !important;
  background-image: none !important;
  color: #FFFFFF !important;
  border-color: #FFFFFF !important;
  box-shadow: none !important;
  text-shadow: none !important;
  transition: none !important;
  animation: none !important;
  scroll-behavior: auto !important;
  caret-color: #00FF00 !important;
}
a, a *, a:visited, a:visited * { color: #00FF00 !important; text-decoration: underline !important; }
a:focus, button:focus, input:focus, textarea:focus, select:focus, [tabindex]:focus {
  outline: 3px solid #00FF00 !important; outline-offset: 1px !important;
}
input, textarea, select, button {
  background-color: #000000 !important; color: #FFFFFF !important;
  border: 2px solid #FFFFFF !important; border-radius: 0 !important;
}
::placeholder { color: #FFFFFF !important; font-style: italic !important; opacity: 1 !important; }
::selection { background-color: #0000FF !important; color: #FFFFFF !important; }
mark { background-color: #FFFF00 !important; color: #000000 !important; }
code, pre, kbd, samp, tt { color: #00FF00 !important; }
img, video, canvas, picture, svg image { background-color: transparent !important; }
svg { fill: currentColor; }
hr { border-color: #FFFFFF !important; }
"""

#: Runs after the stylesheet is (re)applied on every page.
EINK_JS = """
(function(){
  if (!document.querySelector('meta[name=color-scheme]')) {
    var m = document.createElement('meta'); m.name = 'color-scheme'; m.content = 'dark';
    (document.head || document.documentElement).appendChild(m);
  }
  try { history.scrollRestoration = 'manual'; } catch (e) {}
})();
"""
