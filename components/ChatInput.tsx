'use client';

import { useRef, useEffect, KeyboardEvent } from 'react';

interface Props {
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;
  disabled: boolean;
}

export function ChatInput({ value, onChange, onSend, disabled }: Props) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Auto-resize textarea
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 200) + 'px';
  }, [value]);

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (!disabled && value.trim()) onSend();
    }
  }

  return (
    <div className="px-4 pb-4 pt-2">
      <div className="max-w-3xl mx-auto">
        <div className="relative flex items-end gap-2 bg-[#2f2f2f] rounded-2xl border border-[#3a3a3a] focus-within:border-[#555] transition-colors shadow-lg">
          <textarea
            ref={textareaRef}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={disabled}
            placeholder="Message Private KM…"
            rows={1}
            className="flex-1 bg-transparent text-white text-sm placeholder:text-[#555] resize-none px-4 py-3.5 focus:outline-none max-h-[200px] leading-relaxed disabled:opacity-50"
          />

          {/* Send button */}
          <button
            onClick={onSend}
            disabled={disabled || !value.trim()}
            aria-label="Send message"
            className="mb-2.5 mr-2 flex-shrink-0 w-8 h-8 rounded-lg flex items-center justify-center transition-colors disabled:opacity-30 disabled:cursor-not-allowed bg-white hover:bg-gray-200 disabled:bg-[#3a3a3a]"
          >
            {disabled ? (
              // Spinner while streaming
              <svg
                className="animate-spin w-4 h-4 text-[#666]"
                fill="none"
                viewBox="0 0 24 24"
              >
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path
                  className="opacity-75"
                  fill="currentColor"
                  d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
                />
              </svg>
            ) : (
              // Up arrow
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#111" strokeWidth="2.5">
                <path d="M12 19V5M5 12l7-7 7 7" />
              </svg>
            )}
          </button>
        </div>

        <p className="text-center text-xs text-[#444] mt-2">
          Press <kbd className="bg-[#3a3a3a] text-[#666] px-1 py-0.5 rounded text-[10px]">Enter</kbd> to send
          &nbsp;·&nbsp;
          <kbd className="bg-[#3a3a3a] text-[#666] px-1 py-0.5 rounded text-[10px]">Shift + Enter</kbd> for new line
        </p>
      </div>
    </div>
  );
}
