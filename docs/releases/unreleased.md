# Unreleased

Changes since v1.6.0.

## The top bar matches Build-a-Spec's

- **An in-app guide in the top bar.** **How to use**, **Workflows**, **How it works** and **About** open one help
  dialog with a tab strip: a first pass in eight steps, what each note under a file means, how spending, Stop now and
  the pause work, recipes for the usual jobs (fonts across a set, headers and footers from a template, a logo, values
  from a sheet, an issue stamp, a mixed batch, one change after another, a file left untouched, stopped or paused
  work) and how it works in plain words.
- **About replaces the Help menu.** The version, the model, the release notes, the license and its open-source
  components, the author's links, **Check for updates** and **Open diagnostic logs** are on one page. A check you
  ask for now answers there: "Checking for updates…", then "You're up to date", why it could not check, or the
  update with **Download and install**.
- **What the conversation works on**, beside the title from 1280 px wide: "3 documents · 1 template · building on
  your copies".
- **An update you set aside stays one click away.** After **Not now** on the update banner, the top bar keeps a
  "v1.7.0 available — install" pill (from 1280 px wide; About offers it at any width).

Upgrading from 1.6.0 starts from 1.6.0's own window: the update banner's **Download**, or **Help ▾ → Check for
updates** in its top bar.

## Diagnostics

- **The app log says how every job ended.** `spec-surgeon.log` gets one line at the end of each piece of work in the
  window: what was asked for (send, choose or create copies), where it left the conversation (a plan to approve, a
  question, finished copies, the 48-step pause, ready to continue), whether you stopped it, the type of the error
  when it failed, the AI steps used so far, the estimated spending and how long it took. It never records your words
  or the documents' text.
- **The app log says when the window closed.** A clean exit writes `Spec Surgeon closed` again, as the Qt window did
  before 1.6.0; a session that ends without it did not close cleanly.
