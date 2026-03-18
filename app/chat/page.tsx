'use client';

import { useState, useEffect, useRef, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import { Sidebar, type SessionItem } from '@/components/Sidebar';
import { MessageBubble } from '@/components/MessageBubble';
import { ChatInput } from '@/components/ChatInput';

interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
}

export default function ChatPage() {
  const router = useRouter();

  const [username, setUsername] = useState('');
  const [sessions, setSessions] = useState<SessionItem[]>([]);
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [inputValue, setInputValue] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const [streamingContent, setStreamingContent] = useState('');
  const [error, setError] = useState('');
  const [sidebarOpen, setSidebarOpen] = useState(true);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const abortControllerRef = useRef<AbortController | null>(null);

  // ── Auth: get current user ────────────────────────────────────────────────
  useEffect(() => {
    fetch('/api/auth/me')
      .then((r) => {
        if (!r.ok) {
          router.push('/login');
          return null;
        }
        return r.json();
      })
      .then((data) => {
        if (data) setUsername(data.username);
      });
  }, [router]);

  // ── Load sessions list ────────────────────────────────────────────────────
  const loadSessions = useCallback(async () => {
    const res = await fetch('/api/sessions');
    if (res.ok) {
      const data: SessionItem[] = await res.json();
      setSessions(data);
    }
  }, []);

  useEffect(() => {
    loadSessions();
  }, [loadSessions]);

  // ── Load messages for selected session ───────────────────────────────────
  const loadMessages = useCallback(async (sessionId: string) => {
    const res = await fetch(`/api/sessions/${sessionId}/messages`);
    if (res.ok) {
      const data = await res.json();
      setMessages(data);
    }
  }, []);

  useEffect(() => {
    if (currentSessionId) {
      loadMessages(currentSessionId);
    } else {
      setMessages([]);
    }
  }, [currentSessionId, loadMessages]);

  // ── Auto-scroll to bottom ─────────────────────────────────────────────────
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, streamingContent]);

  // ── Handlers ──────────────────────────────────────────────────────────────
  async function handleNewChat() {
    const res = await fetch('/api/sessions', { method: 'POST' });
    if (res.ok) {
      const session: SessionItem = await res.json();
      setSessions((prev) => [session, ...prev]);
      setCurrentSessionId(session.id);
      setMessages([]);
      setInputValue('');
      setError('');
    }
  }

  function handleSelectSession(id: string) {
    if (id === currentSessionId) return;
    setCurrentSessionId(id);
    setError('');
    setStreamingContent('');
  }

  async function handleDeleteSession(id: string) {
    const res = await fetch(`/api/sessions/${id}`, { method: 'DELETE' });
    if (res.ok) {
      setSessions((prev) => prev.filter((s) => s.id !== id));
      if (currentSessionId === id) {
        setCurrentSessionId(null);
        setMessages([]);
      }
    }
  }

  async function handleLogout() {
    await fetch('/api/auth/logout', { method: 'POST' });
    router.push('/login');
  }

  async function handleSend() {
    if (!inputValue.trim() || isStreaming) return;

    setError('');
    let sessionId = currentSessionId;

    // Create session on first message if none selected
    if (!sessionId) {
      const res = await fetch('/api/sessions', { method: 'POST' });
      if (!res.ok) {
        setError('Failed to create a new conversation.');
        return;
      }
      const session: SessionItem = await res.json();
      setSessions((prev) => [session, ...prev]);
      setCurrentSessionId(session.id);
      sessionId = session.id;
    }

    const userMessage = inputValue.trim();
    setInputValue('');
    setIsStreaming(true);
    setStreamingContent('');

    // Optimistically add user message to UI
    const tempUserMsg: Message = {
      id: `temp-user-${Date.now()}`,
      role: 'user',
      content: userMessage,
    };
    setMessages((prev) => [...prev, tempUserMsg]);

    abortControllerRef.current = new AbortController();

    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sessionId, message: userMessage }),
        signal: abortControllerRef.current.signal,
      });

      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.error || 'Failed to get response');
      }

      if (!res.body) throw new Error('No response body');

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let accumulated = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const chunk = decoder.decode(value, { stream: true });
        accumulated += chunk;
        setStreamingContent(accumulated);
      }

      // Commit streamed message into messages list
      const assistantMsg: Message = {
        id: `temp-assistant-${Date.now()}`,
        role: 'assistant',
        content: accumulated,
      };
      setMessages((prev) => [...prev, assistantMsg]);
      setStreamingContent('');

      // Refresh sessions list to update title + updated_at
      await loadSessions();
    } catch (err) {
      if ((err as Error).name === 'AbortError') return;
      setError((err as Error).message || 'Something went wrong. Please try again.');
      setStreamingContent('');
    } finally {
      setIsStreaming(false);
    }
  }

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div className="flex h-screen bg-[#212121] overflow-hidden">
      {/* Mobile sidebar toggle */}
      <button
        onClick={() => setSidebarOpen((v) => !v)}
        className="md:hidden absolute top-3 left-3 z-50 p-2 rounded-lg bg-[#2f2f2f] text-[#8e8ea0] hover:text-white"
        aria-label="Toggle sidebar"
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M3 12h18M3 6h18M3 18h18" />
        </svg>
      </button>

      {/* Sidebar */}
      <div
        className={`${
          sidebarOpen ? 'translate-x-0' : '-translate-x-full'
        } md:translate-x-0 transition-transform duration-200 absolute md:relative z-40 h-full`}
      >
        <Sidebar
          sessions={sessions}
          currentSessionId={currentSessionId}
          username={username}
          onNewChat={handleNewChat}
          onSelectSession={(id) => {
            handleSelectSession(id);
            setSidebarOpen(false);
          }}
          onDeleteSession={handleDeleteSession}
          onLogout={handleLogout}
        />
      </div>

      {/* Overlay for mobile */}
      {sidebarOpen && (
        <div
          className="md:hidden fixed inset-0 bg-black/50 z-30"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Main chat area */}
      <main className="flex-1 flex flex-col overflow-hidden">
        {/* Messages area */}
        <div className="flex-1 overflow-y-auto">
          {messages.length === 0 && !isStreaming ? (
            /* Welcome screen */
            <div className="flex flex-col items-center justify-center h-full text-center px-4">
              <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-blue-500 to-purple-600 flex items-center justify-center mb-6 shadow-xl">
                <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2">
                  <path d="M12 2a10 10 0 1 1 0 20 10 10 0 0 1 0-20z" />
                  <path d="M8 12h8M12 8v8" />
                </svg>
              </div>
              <h2 className="text-2xl font-semibold text-white mb-2">How can I help you today?</h2>
              <p className="text-[#8e8ea0] text-sm max-w-sm">
                Ask me anything. Your conversations are private and separated from other users.
              </p>

              {/* Quick suggestion chips */}
              <div className="mt-8 flex flex-wrap gap-2 justify-center max-w-xl">
                {[
                  'Summarize a document',
                  'Write a draft email',
                  'Explain a concept',
                  'Help debug code',
                  'Create a meeting agenda',
                  'Translate text',
                ].map((suggestion) => (
                  <button
                    key={suggestion}
                    onClick={() => setInputValue(suggestion)}
                    className="px-3.5 py-2 rounded-xl bg-[#2f2f2f] hover:bg-[#3a3a3a] text-[#ececec] text-sm border border-[#3a3a3a] transition-colors"
                  >
                    {suggestion}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="py-6 max-w-4xl mx-auto w-full">
              {messages.map((msg) => (
                <MessageBubble key={msg.id} role={msg.role} content={msg.content} />
              ))}

              {/* Streaming response */}
              {isStreaming && (
                <MessageBubble
                  role="assistant"
                  content={streamingContent}
                  isStreaming={!streamingContent}
                />
              )}

              {/* Error message */}
              {error && (
                <div className="mx-4 md:mx-8 mb-4 px-4 py-3 rounded-xl bg-red-900/20 border border-red-800/40 text-red-400 text-sm">
                  {error}
                </div>
              )}

              <div ref={messagesEndRef} />
            </div>
          )}
        </div>

        {/* Error shown when no messages yet */}
        {error && messages.length === 0 && (
          <div className="max-w-3xl mx-auto w-full px-4 pb-2">
            <div className="px-4 py-3 rounded-xl bg-red-900/20 border border-red-800/40 text-red-400 text-sm">
              {error}
            </div>
          </div>
        )}

        {/* Input */}
        <ChatInput
          value={inputValue}
          onChange={setInputValue}
          onSend={handleSend}
          disabled={isStreaming}
        />
      </main>
    </div>
  );
}
