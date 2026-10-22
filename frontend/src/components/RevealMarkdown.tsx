import { memo, useMemo } from "react";
import ReactMarkdown from "react-markdown";
import type { PluggableList } from "unified";
import { freshFrom, marksIn, sameMarks, splitBlocks } from "../lib/reveal";
import rehypeReveal from "../lib/rehypeReveal";
import { LINK, REMARK_PLUGINS } from "./Markdown";

interface BlockProps {
  text: string;
  marks: number[];
  fresh: number;
  caret: boolean;
}

/** One block of a reply. Memoized on what it shows: a settled block is parsed once, and only the block the words
 *  are flowing into renders each frame. Its elements land straight in the reply's `.md` (React Markdown renders a
 *  fragment), so the blocks read as one markdown text. */
const Block = memo(
  function Block({ text, marks, fresh, caret }: BlockProps) {
    const rehype = useMemo<PluggableList>(() => [[rehypeReveal, { marks, fresh, caret }]], [marks, fresh, caret]);
    return (
      <ReactMarkdown remarkPlugins={REMARK_PLUGINS} rehypePlugins={rehype} components={LINK}>
        {text}
      </ReactMarkdown>
    );
  },
  (a, b) => a.text === b.text && a.caret === b.caret && a.fresh === b.fresh && sameMarks(a.marks, b.marks),
);

interface Props {
  /** The words on the page now (`useReveal().shown`). */
  text: string;
  marks: readonly number[];
  times: readonly number[];
  /** Show the caret after the last word. */
  caret: boolean;
  className?: string;
}

/**
 * A reply's markdown as it is revealed: cut into blocks at its blank lines, each piece that was revealed in the
 * last moments fading in, and the caret after the last word. A reply from history (no marks) renders plain.
 */
export default function RevealMarkdown({ text, marks, times, caret, className = "" }: Props) {
  const blocks = useMemo(() => splitBlocks(text), [text]);
  const fresh = freshFrom(marks, times, performance.now(), text.length);
  return (
    <div className={`md ${className}`}>
      {blocks.map((block, index) => {
        const end = block.start + block.text.length;
        const inside = marksIn(marks, block.start, end);
        return (
          <Block
            key={block.start}
            text={block.text}
            marks={inside}
            fresh={inside.length ? Math.max(0, Math.min(fresh, end) - block.start) : 0}
            caret={caret && index === blocks.length - 1}
          />
        );
      })}
      {caret && blocks.length === 0 && <i className="reveal-caret" aria-hidden="true" />}
    </div>
  );
}
