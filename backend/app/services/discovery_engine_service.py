"""
Discovery Engine (Vertex AI Search) service.

Handles:
  - Grounded conversational search using Gemini Enterprise
  - ACL-aware document retrieval (user identity passed in every request)
  - JD-code post-filtering for CONFIDENTIAL documents
  - Conversation lifecycle management (create / continue / delete)

ACL architecture:
  Level       | Discovery Engine enforcement          | App layer
  ────────────┼───────────────────────────────────────┼────────────────────────
  PUBLIC      | No acl_info on document               | –
  INTERNAL    | acl_info.readers: userId=dept@domain  | –
  RELATE      | acl_info.readers: groupId=relate-*    | –
  CONFIDENTIAL| acl_info.readers: userId=dept@domain  | jd_code check (below)

For CONFIDENTIAL the ACL is set to the department email so DE returns the doc
only to department members. The API then post-filters on structData.required_jd_code
to enforce the finer-grained JD-code restriction.
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

from google.cloud import discoveryengine_v1 as discoveryengine
from google.cloud.discoveryengine_v1.types import SearchResponse

from app.config import Settings, get_settings
from app.models.document import (
    AclInfo,
    AclPrincipal,
    AclReader,
    DocumentStructData,
    RetrievedDocument,
)
from app.models.user import AccessLevel, GCPIdentity
from app.utils.gcp_auth import get_impersonated_credentials

logger = logging.getLogger(__name__)


class DiscoveryEngineService:
    """
    Wraps the Discovery Engine Conversational Search API.

    A new instance is created per-request with the caller's impersonated
    credentials so every API call is made under the correct user identity.
    """

    def __init__(
        self,
        gcp_identity: GCPIdentity,
        settings: Optional[Settings] = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._gcp_identity = gcp_identity
        self._credentials = self._build_credentials()
        self._conv_client = discoveryengine.ConversationalSearchServiceClient(
            credentials=self._credentials
        )
        self._search_client = discoveryengine.SearchServiceClient(
            credentials=self._credentials
        )

    # ── Credentials ────────────────────────────────────────────────────────────

    def _build_credentials(self):
        return get_impersonated_credentials(
            target_email=self._gcp_identity.impersonated_email,
            scopes=self._settings.dwd_scopes,
            sa_key_file=self._settings.google_application_credentials,
        )

    # ── Conversation management ────────────────────────────────────────────────

    def create_conversation(self) -> str:
        """
        Create a new Discovery Engine conversation resource.
        Returns the resource name (persisted on the ChatSession for multi-turn).
        """
        parent = self._settings.datastore_path
        req = discoveryengine.CreateConversationRequest(
            parent=parent,
            conversation=discoveryengine.Conversation(),
        )
        conversation = self._conv_client.create_conversation(request=req)
        logger.debug("Created DE conversation: %s", conversation.name)
        return conversation.name

    def delete_conversation(self, conversation_name: str) -> None:
        """Clean up a conversation resource (call on session close)."""
        try:
            self._conv_client.delete_conversation(
                request=discoveryengine.DeleteConversationRequest(name=conversation_name)
            )
        except Exception as exc:
            logger.warning("Failed to delete conversation %s: %s", conversation_name, exc)

    # ── Grounded conversational search ─────────────────────────────────────────

    def converse(
        self,
        user_message: str,
        conversation_name: str,
        jd_code: Optional[str] = None,
    ) -> Tuple[str, List[RetrievedDocument]]:
        """
        Send a message to an existing conversation and return
        (answer_text, retrieved_documents).

        The Discovery Engine automatically:
          - Retrieves relevant documents from the datastore
          - Filters documents by the caller's ACL (via impersonated credentials)
          - Grounds the Gemini response on the retrieved passages
        """
        filter_expr = self._build_filter(jd_code)

        query_spec = discoveryengine.ConverseConversationRequest.QueryUnderstandingSpec(
            query_classification_spec=discoveryengine.ConverseConversationRequest.QueryUnderstandingSpec.QueryClassificationSpec(
                types=[
                    discoveryengine.ConverseConversationRequest.QueryUnderstandingSpec.QueryClassificationSpec.Type.ADVERSARIAL_QUERY,
                    discoveryengine.ConverseConversationRequest.QueryUnderstandingSpec.QueryClassificationSpec.Type.NON_ANSWER_SEEKING_QUERY,
                ]
            )
        )

        search_spec = discoveryengine.ConverseConversationRequest.SearchSpec(
            search_params=discoveryengine.ConverseConversationRequest.SearchSpec.SearchParams(
                max_return_results=10,
                filter=filter_expr,
                boost_spec=discoveryengine.SearchRequest.BoostSpec(),
            ),
        )

        request = discoveryengine.ConverseConversationRequest(
            name=conversation_name,
            query=discoveryengine.TextInput(input=user_message),
            serving_config=self._settings.serving_config_path,
            safe_search=True,
            query_understanding_spec=query_spec,
            search_spec=search_spec,
            summary_spec=discoveryengine.SearchRequest.ContentSearchSpec.SummarySpec(
                summary_result_count=5,
                include_citations=True,
                ignore_adversarial_query=True,
                ignore_non_summary_seeking_query=True,
                model_prompt_spec=discoveryengine.SearchRequest.ContentSearchSpec.SummarySpec.ModelPromptSpec(
                    preamble=self._settings.gemini_system_prompt
                ),
                model_spec=discoveryengine.SearchRequest.ContentSearchSpec.SummarySpec.ModelSpec(
                    version=self._settings.gemini_model,
                ),
            ),
        )

        response = self._conv_client.converse_conversation(request=request)

        answer_text = ""
        if response.reply and response.reply.summary:
            answer_text = response.reply.summary.summary_text
        elif response.reply:
            answer_text = response.reply.reply

        documents = self._extract_documents(response.search_results, jd_code)
        return answer_text, documents

    # ── Plain document search (non-conversational) ─────────────────────────────

    def search(
        self,
        query: str,
        jd_code: Optional[str] = None,
        page_size: int = 10,
    ) -> List[RetrievedDocument]:
        """
        One-shot search (no conversation context).
        Useful for debug / admin endpoints.
        """
        filter_expr = self._build_filter(jd_code)

        request = discoveryengine.SearchRequest(
            serving_config=self._settings.serving_config_path,
            query=query,
            page_size=page_size,
            filter=filter_expr,
            user_info=discoveryengine.UserInfo(
                user_id=self._gcp_identity.impersonated_email,
            ),
            content_search_spec=discoveryengine.SearchRequest.ContentSearchSpec(
                snippet_spec=discoveryengine.SearchRequest.ContentSearchSpec.SnippetSpec(
                    return_snippet=True,
                    max_snippet_count=3,
                ),
                summary_spec=discoveryengine.SearchRequest.ContentSearchSpec.SummarySpec(
                    summary_result_count=5,
                    include_citations=True,
                ),
                extractive_content_spec=discoveryengine.SearchRequest.ContentSearchSpec.ExtractiveContentSpec(
                    max_extractive_answer_count=3,
                ),
            ),
            spell_correction_spec=discoveryengine.SearchRequest.SpellCorrectionSpec(
                mode=discoveryengine.SearchRequest.SpellCorrectionSpec.Mode.AUTO,
            ),
        )

        response: SearchResponse = self._search_client.search(request=request)
        return self._extract_documents(response.results, jd_code)

    # ── Filter construction ────────────────────────────────────────────────────

    def _build_filter(self, jd_code: Optional[str]) -> str:
        """
        Build a Discovery Engine filter expression that enforces the
        authorization matrix at query time.

        The filter ensures:
          - PUBLIC docs are always returned.
          - INTERNAL docs are returned (DE ACL already restricts to the dept user).
          - RELATE docs are returned (DE ACL restricts to relate group members).
          - CONFIDENTIAL docs are returned ONLY if the user's jd_code matches.

        Note: DE ACL (acl_info) enforces user_id/group_id at the index level.
        This filter adds an extra JD-code gate for CONFIDENTIAL documents at
        query time so we do not rely solely on GWS group membership.
        """
        department = self._gcp_identity.department.upper()

        if jd_code:
            jd_upper = jd_code.upper()
            # Return doc if: not confidential, OR confidential AND jd matches AND same dept
            return (
                f'NOT structData.access_level: ANY("confidential") OR '
                f'(structData.access_level: ANY("confidential") AND '
                f' structData.department: ANY("{department}") AND '
                f' structData.required_jd_code: ANY("{jd_upper}"))'
            )
        else:
            # No jd_code → exclude all confidential docs
            return 'NOT structData.access_level: ANY("confidential")'

    # ── Response parsing ────────────────────────────────────────────────────────

    def _extract_documents(
        self,
        results,
        jd_code: Optional[str],
    ) -> List[RetrievedDocument]:
        """Convert raw DE search results to RetrievedDocument domain objects."""
        documents: List[RetrievedDocument] = []

        for result in results:
            doc = result.document if hasattr(result, "document") else result
            if doc is None:
                continue

            struct_data = self._parse_struct_data(doc)

            # Application-layer JD-code gate for CONFIDENTIAL docs
            if struct_data.access_level == AccessLevel.CONFIDENTIAL:
                if not jd_code or not self._jd_code_matches(
                    struct_data.required_jd_code, jd_code
                ):
                    logger.debug(
                        "Post-filtered confidential doc %s (required=%s user_jd=%s)",
                        doc.id,
                        struct_data.required_jd_code,
                        jd_code,
                    )
                    continue

            snippet = ""
            if hasattr(result, "chunk") and result.chunk:
                snippet = result.chunk.content
            elif doc.derived_struct_data:
                snippets_field = doc.derived_struct_data.get("snippets")
                if snippets_field and snippets_field.list_value.values:
                    first = snippets_field.list_value.values[0]
                    snippet = first.struct_value.fields.get("snippet", None)
                    if snippet:
                        snippet = snippet.string_value

            documents.append(
                RetrievedDocument(
                    id=doc.id or doc.name,
                    struct_data=struct_data,
                    relevance_score=getattr(result, "relevance_score", 0.0),
                    snippet=snippet,
                    uri=struct_data.source_uri,
                )
            )

        return documents

    def _parse_struct_data(self, doc) -> DocumentStructData:
        """Parse structData fields from a Discovery Engine Document proto."""
        sd = {}
        if doc.struct_data:
            for key, val in doc.struct_data.fields.items():
                if val.HasField("string_value"):
                    sd[key] = val.string_value
                elif val.HasField("number_value"):
                    sd[key] = val.number_value
                elif val.HasField("bool_value"):
                    sd[key] = val.bool_value

        access_level_str = sd.get("access_level", "public").lower()
        try:
            access_level = AccessLevel(access_level_str)
        except ValueError:
            access_level = AccessLevel.PUBLIC

        return DocumentStructData(
            title=sd.get("title", ""),
            content=sd.get("content", ""),
            access_level=access_level,
            department=sd.get("department"),
            relate_id=sd.get("relate_id"),
            required_jd_code=sd.get("required_jd_code"),
            source_uri=sd.get("source_uri"),
        )

    @staticmethod
    def _jd_code_matches(required: Optional[str], provided: str) -> bool:
        if not required:
            return True
        return required.upper() == provided.upper()
