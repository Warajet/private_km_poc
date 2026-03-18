import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'Private KM — Organization AI Assistant',
  description: 'Internal chatbot powered by Gemini, shared securely across the organization',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
