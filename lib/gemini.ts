import { GoogleGenerativeAI, HarmCategory, HarmBlockThreshold } from '@google/generative-ai';
import type { Message } from './db';

const MODEL_NAME = process.env.GEMINI_MODEL || 'gemini-1.5-pro';

const SYSTEM_PROMPT =
  process.env.SYSTEM_PROMPT ||
  'You are a helpful AI assistant for the organization. Answer questions clearly and concisely.';

let genAI: GoogleGenerativeAI | null = null;

function getGenAI(): GoogleGenerativeAI {
  if (!genAI) {
    const apiKey = process.env.GEMINI_API_KEY;
    if (!apiKey) {
      throw new Error('GEMINI_API_KEY environment variable is not set');
    }
    genAI = new GoogleGenerativeAI(apiKey);
  }
  return genAI;
}

const SAFETY_SETTINGS = [
  { category: HarmCategory.HARM_CATEGORY_HARASSMENT, threshold: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE },
  { category: HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE },
  { category: HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, threshold: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE },
  { category: HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, threshold: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE },
];

/**
 * Creates a streaming chat response using the full message history.
 * This is how we share one Gemini subscription across users while keeping
 * their chat histories separated in our own database.
 */
export async function streamChatResponse(history: Message[], newUserMessage: string) {
  const model = getGenAI().getGenerativeModel({
    model: MODEL_NAME,
    systemInstruction: SYSTEM_PROMPT,
    safetySettings: SAFETY_SETTINGS,
  });

  // Reconstruct Gemini history from our stored messages
  const geminiHistory = history.map((m) => ({
    role: m.role === 'user' ? 'user' : 'model',
    parts: [{ text: m.content }],
  }));

  const chat = model.startChat({ history: geminiHistory });
  return chat.sendMessageStream(newUserMessage);
}

export { MODEL_NAME };
