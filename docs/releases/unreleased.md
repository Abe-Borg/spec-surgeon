# Unreleased

Changes since v1.6.0.

## Lighter on your computer

- **No more unused AI model.** PyMuPDF 2.0 (released 2026-10-19, two days before v1.6.0 was built) loads a
  page-layout neural network, with onnxruntime and numpy, every time it is imported. Spec Surgeon never uses it, but
  v1.6.0 carried it: about 75 MB more memory and 13 more threads in the window, and roughly 160 MB more in the
  installer. PyMuPDF is now kept below 2.0, and the installed-app check fails if the model ever comes back.
- **One worker by default.** Planning, applying and verifying a batch used one thread per processor core (up to
  eight). Python runs that work one thread at a time, so the extra threads only cost memory: on eight 53-page
  sections, eight threads took 19% longer than one and 2.7 times the memory (471 MB against 177 MB). Batches now
  use one worker unless `--workers` asks for more; rendering keeps its own parallel workers.
- **A quiet window while it waits.** While Word renders, the window used to redraw itself on every screen refresh:
  the reply's typing animation kept running with nothing to type, and the status line's shimmer repainted the text
  each frame. The typing animation now stops when it has caught up, the status line fades gently instead, and the
  side panel, header and input box no longer redraw on every streamed word. Measured on the page: about 1% of a
  processor core while waiting, down from 6–8%. Finished messages are no longer re-read each time the window
  refreshes.
