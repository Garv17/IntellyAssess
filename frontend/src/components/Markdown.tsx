import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

interface Props {
  children: string;
}

/** Renders a `_md` field as actual Markdown (paragraphs, lists, code blocks, tables,
    images) instead of literal text. react-markdown sanitizes by default (no raw HTML
    passthrough), so this is safe to use directly on admin-authored content. */
export default function Markdown({ children }: Props) {
  return (
    <div className="markdown-body">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{children}</ReactMarkdown>
    </div>
  );
}
