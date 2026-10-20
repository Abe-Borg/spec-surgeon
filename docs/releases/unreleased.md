# Unreleased

Changes since v1.5.0.

## The window is one conversation that delivers

- **One request, one plan.** Edits that touch the same parts of a file (a font change and a header/footer template,
  restyling beside direct formatting, a numbering change beside a restyle) are composed into ordered steps by the
  engine itself and previewed, approved and run as one plan. Nothing is asked of you in two steps any more.
- **The next request builds on the result.** After copies are created, the next thing you ask for is applied on top
  of them; your originals stay as they are. "Start over from the originals" is one tap away.
- **You see the work.** The app shows what it is reading, the model's reply as it is written, the plan before any page
  renders, the pages as they render and the copies as they are written. Stop keeps what landed.
- **Defaults first.** The assistant states its assumptions and goes ahead; it asks at most three questions, each with
  the answer it recommends, and offers replies you can tap.
- **A new window.** The Qt window is gone. The window is now a web page in a native window (Edge WebView2 on
  Windows), in the style of Build-a-Spec: a chat on the left, your documents, the plan, the pages and the copies on
  the right.
- **Fewer, faster model calls.** Every operation's parameters ride the cached prompt instead of a round trip per
  operation; the conversation runs at the model's default effort; the thinking summary is shown.
- **Render once.** One Word instance renders a whole batch; renders are cached by content, so the run never renders
  again what the preview rendered.

Pending on the owner's machine: the installed Windows app with WebView2 and Word, a live request over real
specifications, and the measured time and cost per request.
