import { memo, type ComponentProps } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/**
 * Markdown as the window renders it, in both panes: the model's replies and
 * questions in the chat, and the plan's sentences, assumptions, findings and
 * reasons on the paper cards. `paper` takes the paper tone; `inline` renders a
 * sentence inside a line (a list item, a label) without paragraph blocks.
 *
 * A link in model text (untrusted) opens in the system browser: in the native
 * window a plain anchor would navigate the app's own page away, taking the job
 * stream and the draft with it, with no way back.
 */

/** One plugin array for every ReactMarkdown here: an inline `[remarkGfm]` is a
 *  fresh array per render, which reads as a changed prop to anything that
 *  compares it. */
export const REMARK_PLUGINS = [remarkGfm];

export const LINK = {
  a: ({ node: _node, ...props }: ComponentProps<"a"> & { node?: unknown }) => (
    <a {...props} target="_blank" rel="noreferrer noopener" />
  ),
};

/** Inline: a paragraph becomes a span, so a sentence sits inside a line. */
const INLINE = {
  ...LINK,
  p: ({ node: _node, ...props }: ComponentProps<"p"> & { node?: unknown }) => <span {...props} />,
};

interface Props {
  text: string;
  inline?: boolean;
  paper?: boolean;
  className?: string;
}

function Markdown({ text, inline = false, paper = false, className = "" }: Props) {
  if (!text) return null;
  const classes = ["md", paper ? "md-paper" : "", inline ? "md-inline" : "", className].filter(Boolean).join(" ");
  const body = (
    <ReactMarkdown remarkPlugins={REMARK_PLUGINS} components={inline ? INLINE : LINK}>
      {text}
    </ReactMarkdown>
  );
  return inline ? <span className={classes}>{body}</span> : <div className={classes}>{body}</div>;
}

/** Memoized: a settled message or a plan sentence is parsed once, not on every animation frame. */
export default memo(Markdown);
