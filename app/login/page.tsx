'use client';

import { useState, FormEvent } from 'react';
import { useRouter } from 'next/navigation';

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [email, setEmail] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError('');
    setLoading(true);

    const endpoint = mode === 'register' ? '/api/auth/register' : '/api/auth/login';
    const body =
      mode === 'register' ? { username, password, email: email || undefined } : { username, password };

    try {
      const res = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });

      const data = await res.json();

      if (res.ok) {
        router.push('/chat');
        router.refresh();
      } else {
        setError(data.error || 'Something went wrong. Please try again.');
      }
    } catch {
      setError('Network error. Please check your connection.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-[#212121]">
      <div className="w-full max-w-sm px-4">
        {/* Logo */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-gradient-to-br from-blue-500 to-purple-600 mb-4 shadow-lg">
            <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2">
              <path d="M12 2a10 10 0 1 1 0 20 10 10 0 0 1 0-20z" />
              <path d="M8 12h8M12 8v8" />
            </svg>
          </div>
          <h1 className="text-2xl font-semibold text-white">Private KM</h1>
          <p className="text-[#8e8ea0] text-sm mt-1">Organization AI Assistant</p>
        </div>

        {/* Card */}
        <div className="bg-[#2f2f2f] rounded-2xl p-6 shadow-xl">
          {/* Tab switcher */}
          <div className="flex rounded-lg bg-[#1a1a1a] p-1 mb-6">
            <button
              onClick={() => { setMode('login'); setError(''); }}
              className={`flex-1 py-1.5 text-sm rounded-md font-medium transition-colors ${
                mode === 'login'
                  ? 'bg-[#2f2f2f] text-white shadow'
                  : 'text-[#8e8ea0] hover:text-white'
              }`}
            >
              Sign in
            </button>
            <button
              onClick={() => { setMode('register'); setError(''); }}
              className={`flex-1 py-1.5 text-sm rounded-md font-medium transition-colors ${
                mode === 'register'
                  ? 'bg-[#2f2f2f] text-white shadow'
                  : 'text-[#8e8ea0] hover:text-white'
              }`}
            >
              Register
            </button>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-xs font-medium text-[#8e8ea0] mb-1.5">Username</label>
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className="w-full bg-[#1a1a1a] text-white rounded-lg px-3.5 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 placeholder:text-[#555] transition"
                placeholder="your-username"
                autoComplete="username"
                required
              />
            </div>

            {mode === 'register' && (
              <div>
                <label className="block text-xs font-medium text-[#8e8ea0] mb-1.5">
                  Email <span className="text-[#555]">(optional)</span>
                </label>
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="w-full bg-[#1a1a1a] text-white rounded-lg px-3.5 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 placeholder:text-[#555] transition"
                  placeholder="you@company.com"
                  autoComplete="email"
                />
              </div>
            )}

            <div>
              <label className="block text-xs font-medium text-[#8e8ea0] mb-1.5">Password</label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full bg-[#1a1a1a] text-white rounded-lg px-3.5 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 placeholder:text-[#555] transition"
                placeholder="••••••••"
                autoComplete={mode === 'register' ? 'new-password' : 'current-password'}
                required
              />
            </div>

            {error && (
              <div className="text-red-400 text-sm bg-red-900/20 border border-red-800/40 rounded-lg px-3 py-2">
                {error}
              </div>
            )}

            <button
              type="submit"
              disabled={loading}
              className="w-full bg-blue-600 hover:bg-blue-500 disabled:opacity-50 disabled:cursor-not-allowed text-white rounded-lg py-2.5 text-sm font-medium transition-colors mt-2"
            >
              {loading
                ? 'Please wait…'
                : mode === 'register'
                ? 'Create Account'
                : 'Sign In'}
            </button>
          </form>
        </div>

        {mode === 'register' && (
          <p className="text-center text-xs text-[#555] mt-4 px-4">
            By creating an account you agree to use this tool responsibly within your organization.
          </p>
        )}
      </div>
    </div>
  );
}
