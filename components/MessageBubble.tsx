'use client';

import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism';
import { useState } from 'react';

interface Props {
  role: 'user' | 'assistant';
  content: string;
  isStreaming?: boolean;
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    await navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <button
      onClick={handleCopy}
      className="absolute top-2 right-2 text-xs px-2 py-1 rounded bg-[#3a3a3a] text-[#8e8ea0] hover:bg-[#4a4a4a] hover:text-white transition-colors"
    >
      {copied ? 'Copied!' : 'Copy'}
    </button>
  );
}

export function MessageBubble({ role, content, isStreaming }: Props) {
  if (role === 'user') {
    return (
      <div className="flex justify-end mb-6 message-appear px-4 md:px-8">
        <div className="max-w-[80%] md:max-w-[70%] bg-[#2f2f2f] text-white rounded-3xl rounded-tr-lg px-4 py-3 text-sm leading-relaxed whitespace-pre-wrap break-words">
          {content}
        </div>
      </div>
    );
  }

  return (
    <div className="flex gap-3 mb-6 message-appear px-4 md:px-8">
      {/* AI Avatar */}
      <div className="flex-shrink-0 w-8 h-8 rounded-full bg-gradient-to-br from-blue-500 to-purple-600 flex items-center justify-center mt-0.5">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2.5">
          <path d="M12 2a10 10 0 1 1 0 20 10 10 0 0 1 0-20z" />
          <path d="M8 12h8M12 8v8" />
        </svg>
      </div>

      {/* Message content */}
      <div className="flex-1 min-w-0 max-w-[85%] md:max-w-[75%] text-[#ececec] text-sm leading-relaxed">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            code({ inline, className, children, ...props }: any) {
              const match = /language-(\w+)/.exec(className || '');
              const codeString = String(children).replace(/\n$/, '');
              return !inline && match ? (
                <div className="relative my-3 rounded-xl overflow-hidden text-[13px]">
                  <div className="flex items-center justify-between px-4 py-2 bg-[#1a1a1a] text-[#8e8ea0] text-xs">
                    <span>{match[1]}</span>
                    <CopyButton text={codeString} />
                  </div>
                  <SyntaxHighlighter
                    style={oneDark}
                    language={match[1]}
                    PreTag="div"
                    customStyle={{ margin: 0, borderRadius: 0, background: '#0d0d0d', padding: '1rem' }}
                  >
                    {codeString}
                  </SyntaxHighlighter>
                </div>
              ) : (
                <code
                  className="bg-[#1e1e1e] text-blue-300 px-1.5 py-0.5 rounded text-[13px] font-mono"
                  {...props}
                >
                  {children}
                </code>
              );
            },
            p: ({ children }) => <p className="mb-3 last:mb-0">{children}</p>,
            ul: ({ children }) => (
              <ul className="list-disc list-outside pl-5 mb-3 space-y-1">{children}</ul>
            ),
            ol: ({ children }) => (
              <ol className="list-decimal list-outside pl-5 mb-3 space-y-1">{children}</ol>
            ),
            li: ({ children }) => <li className="leading-relaxed">{children}</li>,
            strong: ({ children }) => (
              <strong className="font-semibold text-white">{children}</strong>
            ),
            em: ({ children }) => <em className="italic">{children}</em>,
            h1: ({ children }) => (
              <h1 className="text-xl font-bold mb-3 mt-4 first:mt-0 text-white">{children}</h1>
            ),
            h2: ({ children }) => (
              <h2 className="text-lg font-bold mb-2 mt-4 first:mt-0 text-white">{children}</h2>
            ),
            h3: ({ children }) => (
              <h3 className="text-base font-semibold mb-2 mt-3 first:mt-0 text-white">{children}</h3>
            ),
            blockquote: ({ children }) => (
              <blockquote className="border-l-4 border-[#555] pl-4 italic my-3 text-[#a0a0a0]">
                {children}
              </blockquote>
            ),
            hr: () => <hr className="border-[#3a3a3a] my-4" />,
            table: ({ children }) => (
              <div className="overflow-x-auto my-4 rounded-lg border border-[#3a3a3a]">
                <table className="min-w-full text-sm border-collapse">{children}</table>
              </div>
            ),
            thead: ({ children }) => <thead className="bg-[#1a1a1a]">{children}</thead>,
            th: ({ children }) => (
              <th className="px-4 py-2 text-left font-semibold text-white border-b border-[#3a3a3a]">
                {children}
              </th>
            ),
            td: ({ children }) => (
              <td className="px-4 py-2 border-b border-[#2a2a2a] last:border-0">{children}</td>
            ),
          }}
        >
          {content}
        </ReactMarkdown>

        {isStreaming && <span className="streaming-cursor" aria-hidden />}
      </div>
    </div>
  );
}
