/**
 * lib/gemini.ts
 *
 * Unified Gemini client that supports two authentication modes:
 *
 *  1. Vertex AI (Gemini Enterprise) — used when GOOGLE_CLOUD_PROJECT is set.
 *     Authentication is automatic via Application Default Credentials (ADC).
 *     On Cloud Run, the attached service account is used — no key files needed.
 *     Required env vars: GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION (optional)
 *
 *  2. Google AI Studio — used when GEMINI_API_KEY is set (local development).
 *     Required env vars: GEMINI_API_KEY
 *
 * If both are set, Vertex AI takes precedence.
 */

import type { Message } from './db';

const MODEL_NAME = process.env.GEMINI_MODEL || 'gemini-1.5-pro';

const SYSTEM_PROMPT =
  process.env.SYSTEM_PROMPT ||
  'You are a helpful AI assistant for the organization. Answer questions clearly and concisely.';

// ─── Vertex AI path (Gemini Enterprise) ──────────────────────────────────────

async function streamViaVertexAI(history: Message[], newUserMessage: string) {
  // Dynamically imported so the package is optional for local dev
  const { VertexAI, HarmCategory, HarmBlockThreshold } = await import('@google-cloud/vertexai');

  const vertex = new VertexAI({
    project: process.env.GOOGLE_CLOUD_PROJECT!,
    location: process.env.GOOGLE_CLOUD_LOCATION || 'us-central1',
  });

  const model = vertex.getGenerativeModel({
    model: MODEL_NAME,
    systemInstruction: { role: 'system', parts: [{ text: SYSTEM_PROMPT }] },
    safetySettings: [
      { category: HarmCategory.HARM_CATEGORY_HARASSMENT,        threshold: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE },
      { category: HarmCategory.HARM_CATEGORY_HATE_SPEECH,       threshold: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE },
      { category: HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, threshold: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE },
      { category: HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, threshold: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE },
    ],
  });

  const geminiHistory = history.map((m) => ({
    role: m.role === 'user' ? 'user' : 'model',
    parts: [{ text: m.content }],
  }));

  const chat = model.startChat({ history: geminiHistory });
  const response = await chat.sendMessageStream(newUserMessage);

  // Normalise to an AsyncIterable that yields { text(): string }
  return {
    stream: (async function* () {
      for await (const chunk of response.stream) {
        const text = chunk.candidates?.[0]?.content?.parts?.[0]?.text ?? '';
        if (text) yield { text: () => text };
      }
    })(),
  };
}

// ─── Google AI Studio path (local development) ───────────────────────────────

async function streamViaAIStudio(history: Message[], newUserMessage: string) {
  const { GoogleGenerativeAI, HarmCategory, HarmBlockThreshold } = await import('@google/generative-ai');

  const genAI = new GoogleGenerativeAI(process.env.GEMINI_API_KEY!);

  const model = genAI.getGenerativeModel({
    model: MODEL_NAME,
    systemInstruction: SYSTEM_PROMPT,
    safetySettings: [
      { category: HarmCategory.HARM_CATEGORY_HARASSMENT,        threshold: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE },
      { category: HarmCategory.HARM_CATEGORY_HATE_SPEECH,       threshold: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE },
      { category: HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, threshold: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE },
      { category: HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, threshold: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE },
    ],
  });

  const geminiHistory = history.map((m) => ({
    role: m.role === 'user' ? 'user' : 'model',
    parts: [{ text: m.content }],
  }));

  const chat = model.startChat({ history: geminiHistory });
  return chat.sendMessageStream(newUserMessage);
}

// ─── Public API ───────────────────────────────────────────────────────────────

/**
 * Streams a chat response. Automatically selects the authentication path:
 * - Vertex AI when GOOGLE_CLOUD_PROJECT is set   (production / Cloud Run)
 * - Google AI Studio when GEMINI_API_KEY is set  (local development)
 */
export async function streamChatResponse(history: Message[], newUserMessage: string) {
  if (process.env.GOOGLE_CLOUD_PROJECT) {
    return streamViaVertexAI(history, newUserMessage);
  }

  if (process.env.GEMINI_API_KEY) {
    return streamViaAIStudio(history, newUserMessage);
  }

  throw new Error(
    'No Gemini credentials configured. ' +
    'Set GOOGLE_CLOUD_PROJECT (Vertex AI / Gemini Enterprise) or ' +
    'GEMINI_API_KEY (Google AI Studio) in your environment.'
  );
}

export { MODEL_NAME };
