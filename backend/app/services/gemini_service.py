"""
GeminiService — generates a grounded conversational response using Vertex AI.

Because we now aggregate results from multiple datastores (each with its own
ACL enforcement), we can no longer use Discovery Engine's built-in
ConversationalSearchService as the single entry point.  Instead:

  1. DiscoveryEngineService.search_target() queries each datastore bucket.
  2. Results are merged and ranked by relevance.
  3. GeminiService.generate() builds a grounded prompt and calls Gemini via
     the Vertex AI SDK, using the same impersonated credentials.

Auth note:
  The Vertex AI client is initialised with the impersonated credentials so
  that any audit log shows the department identity, not the raw service account.
"""

from __future__ import annotations

import logging
from typing import List, Optional

import vertexai
from vertexai.generative_models import (
    Content,
    GenerationConfig,
    GenerativeModel,
    HarmBlockThreshold,
    HarmCategory,
    Part,
)

from app.config import Settings, get_settings
from app.models.chat import ChatMessage, MessageRole
from app.models.document import RetrievedDocument
from app.models.user import GCPIdentity

logger = logging.getLogger(__name__)

_SAFETY_SETTINGS = {
    HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
    HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
    HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
    HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
}

_GROUNDED_PROMPT_TEMPLATE = """\
You are an enterprise knowledge assistant. Answer the user's question using \
ONLY the information in the retrieved documents below. If the answer cannot \
be found in the documents, say so clearly — do not invent information.

When citing information, reference the document title in parentheses, \
e.g. (Source: Document Title).

──── Retrieved Documents ────
{context}
────────────────────────────

User question: {question}"""

_NO_CONTEXT_PROMPT_TEMPLATE = """\
You are an enterprise knowledge assistant. No relevant documents were found \
in the authorised knowledge base for the following question.

Inform the user that no relevant information is available in the documents \
they are authorised to access, and suggest they contact their administrator \
or try rephrasing their query.

User question: {question}"""


class GeminiService:
    """
    Wraps the Vertex AI Gemini model.  One instance per request, scoped to the
    caller's impersonated credentials.
    """

    def __init__(
        self,
        gcp_identity: GCPIdentity,
        credentials,
        settings: Optional[Settings] = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._gcp_identity = gcp_identity
        self._credentials = credentials
        self._init_vertex()

    def _init_vertex(self) -> None:
        vertexai.init(
            project=self._settings.gcp_project_id,
            location=self._settings.gcp_location if self._settings.gcp_location != "global" else "us-central1",
            credentials=self._credentials,
        )

    def generate(
        self,
        user_message: str,
        history: List[ChatMessage],
        retrieved_docs: List[RetrievedDocument],
    ) -> str:
        """
        Generate a grounded response.

        history   – previous ChatMessage objects (user + assistant turns)
        retrieved_docs – merged, ranked documents from all accessible datastores
        """
        model = GenerativeModel(
            model_name=self._settings.gemini_model,
            system_instruction=self._settings.gemini_system_prompt,
            generation_config=GenerationConfig(
                temperature=0.1,
                max_output_tokens=2048,
                top_p=0.95,
            ),
            safety_settings=_SAFETY_SETTINGS,
        )

        gemini_history = self._build_history(history)
        grounded_message = self._build_grounded_message(user_message, retrieved_docs)

        chat = model.start_chat(history=gemini_history)
        try:
            response = chat.send_message(grounded_message)
            return response.text
        except Exception as exc:
            logger.error("Gemini generation error: %s", exc, exc_info=True)
            raise

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _build_history(history: List[ChatMessage]) -> List[Content]:
        """Convert domain ChatMessage list to Vertex AI Content list."""
        contents = []
        for msg in history:
            if msg.role == MessageRole.SYSTEM:
                continue
            role = "user" if msg.role == MessageRole.USER else "model"
            contents.append(Content(role=role, parts=[Part.from_text(msg.content)]))
        return contents

    def _build_grounded_message(
        self,
        user_message: str,
        docs: List[RetrievedDocument],
    ) -> str:
        if not docs:
            return _NO_CONTEXT_PROMPT_TEMPLATE.format(question=user_message)

        context_parts = []
        for i, doc in enumerate(docs, 1):
            title = doc.struct_data.title or doc.id
            bucket = doc.struct_data.access_level.value
            text = doc.snippet or doc.struct_data.content or "(no text)"
            context_parts.append(
                f"[{i}] {title} (bucket: {bucket})\n{text}"
            )

        context = "\n\n".join(context_parts)
        return _GROUNDED_PROMPT_TEMPLATE.format(
            context=context,
            question=user_message,
        )
