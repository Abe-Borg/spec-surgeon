"""The window: a local server (``server``) around one ``DocumentTask``, the page it serves (``static/``, built from
``frontend/``), a native WebView2 shell (``shell``) and the headless update flow (``updates``). The contract between
the page and the server is docs/WEB_API.md."""
