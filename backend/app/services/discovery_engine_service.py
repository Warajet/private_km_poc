"""
DiscoveryEngineService — ConversationalSearchServiceClient.answer_query()
                         with DataStoreSpecs for multi-bucket access control.

How DataStoreSpecs work
────────────────────────
Instead of making N separate answer_query() calls (one per accessible bucket),
a single request carries a list of DataStoreSpec entries — one per bucket that
the routing layer has authorised for this user.

  AnswerQueryRequest(
      serving_config = engine_serving_config,   ← engine-level (required)
      query          = Query(text=user_message),
      session        = session_name,            ← one per chat session
      search_spec    = SearchSpec(
          search_params = SearchParams(
              data_store_specs = [
                  SearchRequest.DataStoreSpec(data_store="…/dataStores/public-ds"),
                  SearchRequest.DataStoreSpec(data_store="…/dataStores/internal-a-ds"),
                  SearchRequest.DataStoreSpec(data_store="…/dataStores/relate-ab-ds"),
                  SearchRequest.DataStoreSpec(
                      data_store="…/dataStores/confidential-ds",
                      filter='structData.department: ANY("A") AND '
                             'structData.required_jd_code: ANY("ENG001")',
                  ),
              ],
          ),
      ),
  )

Discovery Engine:
  • Searches all listed datastores with a single Gemini grounding pass.
  • Enforces acl_info on every document via the impersonated credentials.
  • Applies the per-DataStoreSpec filter before the confidential datastore is
    searched (JD-code gate at the document level on top of ACL).
  • Returns one coherent Answer with citations referencing both datastores.
  • Returns one session resource name — pass it on the next turn to continue
    the multi-turn conversation.

Architecture note
──────────────────
  Layer 1 (DatastoreRoutingService) → determines WHICH DataStoreSpec entries
    to include, i.e. WHICH buckets this user may query.
  Layer 2 (this service)            → answer_query() enforces ACL inside each
    listed bucket via impersonated credentials + optional per-spec filter.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional

from google.cloud import discoveryengine_v1 as discoveryengine

from app.config import Settings, get_settings
from app.models.datastore import DatastoreTarget
from app.models.document import DocumentStructData, RetrievedDocument
from app.models.user import AccessLevel, GCPIdentity
from app.utils.gcp_auth import get_impersonated_credentials

logger = logging.getLogger(__name__)


@dataclass
class AnswerResult:
    """
    The result of a single answer_query() call spanning all accessible buckets.
    """
    answer_text: str
    references: List[RetrievedDocument] = field(default_factory=list)
    # Updated DE session resource name — store on ChatSession for next turn
    session_name: str = ""
    grounded: bool = False


class DiscoveryEngineService:
    """
    One instance per request.  Builds and executes a single answer_query()
    call whose DataStoreSpecs list is determined by the routing layer.
    """

    def __init__(
        self,
        gcp_identity: GCPIdentity,
        settings: Optional[Settings] = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._gcp_identity = gcp_identity
        self._credentials = get_impersonated_credentials(
            target_email=gcp_identity.impersonated_email,
            scopes=self._settings.dwd_scopes,
            sa_key_file=self._settings.google_application_credentials,
        )
        self._client = discoveryengine.ConversationalSearchServiceClient(
            credentials=self._credentials
        )

    # ── Public API ─────────────────────────────────────────────────────────────

    def answer_query(
        self,
        query: str,
        targets: List[DatastoreTarget],
        existing_session: str = "",
    ) -> AnswerResult:
        """
        Execute a single answer_query() for all accessible datastore buckets.

        targets          – List[DatastoreTarget] from DatastoreRoutingService.
                           Each entry becomes one DataStoreSpec in the request.
        existing_session – DE session resource name from the previous turn.
                           Empty string on the first turn (auto-create).
        """
        if not targets:
            return AnswerResult(answer_text="", session_name="")

        data_store_specs = self._build_data_store_specs(targets)
        session = existing_session or self._settings.engine_session_auto_path()

        AQR = discoveryengine.AnswerQueryRequest
        request = AQR(
            serving_config=self._settings.engine_serving_config_path,
            # User's query for this turn
            query=discoveryengine.Query(text=query),
            # Continues the multi-turn conversation in DE (auto-creates on first turn)
            session=session,
            # Stable pseudo-id for DE analytics / session tracking
            user_pseudo_id=self._gcp_identity.impersonated_email,
            # ── Multi-bucket access control via SearchSpec.SearchParams ────────
            # DataStoreSpec entries live inside search_spec.search_params.
            # Each entry names one accessible bucket with an optional filter.
            # ACL (acl_info) is enforced automatically by DE against the
            # impersonated credentials; the filter adds the extra JD-code gate
            # for the CONFIDENTIAL bucket.
            search_spec=AQR.SearchSpec(
                search_params=AQR.SearchSpec.SearchParams(
                    data_store_specs=data_store_specs,
                ),
            ),
            # ── Query understanding ────────────────────────────────────────────
            query_understanding_spec=self._build_query_understanding_spec(),
            # ── Answer generation (Gemini) ─────────────────────────────────────
            answer_generation_spec=self._build_answer_generation_spec(),
            # ── Related questions (optional UX) ───────────────────────────────
            related_questions_spec=AQR.RelatedQuestionsSpec(
                enable=False,
            ),
        )

        logger.info(
            "answer_query: user=%s buckets=%s session=%s",
            self._gcp_identity.impersonated_email,
            [t.label for t in targets],
            session,
        )

        response = self._client.answer_query(request=request)
        return self._parse_response(response, session)

    # ── DataStoreSpecs construction ────────────────────────────────────────────

    def _build_data_store_specs(
        self,
        targets: List[DatastoreTarget],
    ) -> List[discoveryengine.SearchRequest.DataStoreSpec]:
        """
        Convert routing targets to SearchRequest.DataStoreSpec objects.

        These are placed inside AnswerQueryRequest.SearchSpec.SearchParams.
        PUBLIC / INTERNAL / RELATE  → spec with no filter (ACL handles it)
        CONFIDENTIAL                → spec with dept + JD-code filter expression
        """
        specs = []
        for target in targets:
            filter_expr = self._build_filter(target)
            spec = discoveryengine.SearchRequest.DataStoreSpec(
                data_store=self._settings.datastore_resource_name(target.datastore_id),
                filter=filter_expr,
            )
            specs.append(spec)
            logger.debug(
                "DataStoreSpec: bucket=%s ds=%s filter=%r",
                target.label,
                target.datastore_id,
                filter_expr or "(none)",
            )
        return specs

    def _build_filter(self, target: DatastoreTarget) -> str:
        """
        Only CONFIDENTIAL targets need an explicit filter.
        All other buckets rely entirely on Discovery Engine ACL (acl_info)
        evaluated against the impersonated credentials.
        """
        if target.access_level != AccessLevel.CONFIDENTIAL:
            return ""

        dept = self._gcp_identity.department.upper()
        jd = (target.jd_code or "").upper()
        if not jd:
            # No JD code → safety net: match nothing in the confidential bucket
            return 'structData.required_jd_code: ANY("__never__")'

        return (
            f'structData.department: ANY("{dept}") AND '
            f'structData.required_jd_code: ANY("{jd}")'
        )

    # ── Request spec builders ──────────────────────────────────────────────────

    def _build_query_understanding_spec(
        self,
    ) -> discoveryengine.AnswerQueryRequest.QueryUnderstandingSpec:
        QUS = discoveryengine.AnswerQueryRequest.QueryUnderstandingSpec
        return QUS(
            query_rephraser_spec=QUS.QueryRephraserSpec(
                disable=False,
                max_rephrase_steps=1,
            ),
            query_classification_spec=QUS.QueryClassificationSpec(
                types=[
                    QUS.QueryClassificationSpec.Type.ADVERSARIAL_QUERY,
                    QUS.QueryClassificationSpec.Type.NON_ANSWER_SEEKING_QUERY,
                ]
            ),
        )

    def _build_answer_generation_spec(
        self,
    ) -> discoveryengine.AnswerQueryRequest.AnswerGenerationSpec:
        AGS = discoveryengine.AnswerQueryRequest.AnswerGenerationSpec
        return AGS(
            model_spec=AGS.ModelSpec(
                model_version=self._settings.gemini_model,
            ),
            prompt_spec=AGS.PromptSpec(
                preamble=self._settings.gemini_system_prompt,
            ),
            include_citations=True,
            answer_language_code="en",
            ignore_adversarial_query=True,
            ignore_non_answer_seeking_query=True,
        )

    # ── Response parsing ───────────────────────────────────────────────────────

    def _parse_response(
        self,
        response: discoveryengine.AnswerQueryResponse,
        fallback_session: str,
    ) -> AnswerResult:
        answer_text = ""
        if response.answer and response.answer.answer_text:
            answer_text = response.answer.answer_text

        references = self._extract_references(response)
        # DE returns the (possibly newly created) session resource name
        session_name = response.session or fallback_session

        return AnswerResult(
            answer_text=answer_text,
            references=references,
            session_name=session_name,
            grounded=bool(references),
        )

    def _extract_references(
        self,
        response: discoveryengine.AnswerQueryResponse,
    ) -> List[RetrievedDocument]:
        if not response.answer or not response.answer.references:
            return []

        docs: List[RetrievedDocument] = []
        seen: set[str] = set()

        for ref in response.answer.references:
            doc = self._parse_reference(ref)
            if doc and doc.id not in seen:
                seen.add(doc.id)
                docs.append(doc)

        # Sort by relevance score descending
        docs.sort(key=lambda d: d.relevance_score, reverse=True)
        return docs[:20]

    def _parse_reference(
        self,
        ref: discoveryengine.Answer.Reference,
    ) -> Optional[RetrievedDocument]:
        try:
            # ── ChunkInfo (chunked / enterprise datastores) ──────────────────
            if ref.chunk_info and ref.chunk_info.content:
                ci = ref.chunk_info
                meta = ci.document_metadata
                doc_id = meta.document or meta.uri or "unknown"
                struct_data = self._struct_data_from_proto(
                    getattr(meta, "struct_data", None)
                )
                struct_data.title = struct_data.title or meta.title or ""
                struct_data.source_uri = struct_data.source_uri or meta.uri or ""
                return RetrievedDocument(
                    id=doc_id,
                    struct_data=struct_data,
                    snippet=ci.content,
                    relevance_score=ci.relevance_score or 0.0,
                    uri=meta.uri or "",
                )

            # ── UnstructuredDocumentInfo (HTML / GCS docs) ───────────────────
            if ref.unstructured_document_info:
                udi = ref.unstructured_document_info
                doc_id = udi.document or udi.uri or "unknown"
                snippet, score = "", 0.0
                if udi.chunk_contents:
                    best = max(
                        udi.chunk_contents,
                        key=lambda c: c.relevance_score or 0.0,
                        default=udi.chunk_contents[0],
                    )
                    snippet = best.content
                    score = best.relevance_score or 0.0
                struct_data = self._struct_data_from_proto(
                    getattr(udi, "struct_data", None)
                )
                struct_data.title = struct_data.title or udi.title or ""
                struct_data.source_uri = struct_data.source_uri or udi.uri or ""
                return RetrievedDocument(
                    id=doc_id,
                    struct_data=struct_data,
                    snippet=snippet,
                    relevance_score=score,
                    uri=udi.uri or "",
                )
        except Exception as exc:
            logger.warning("Failed to parse reference: %s", exc)
        return None

    @staticmethod
    def _struct_data_from_proto(struct_proto) -> DocumentStructData:
        sd: dict = {}
        if struct_proto:
            for key, val in struct_proto.fields.items():
                if val.HasField("string_value"):
                    sd[key] = val.string_value
                elif val.HasField("number_value"):
                    sd[key] = val.number_value
                elif val.HasField("bool_value"):
                    sd[key] = val.bool_value

        raw_level = sd.get("access_level", "public").lower()
        try:
            access_level = AccessLevel(raw_level)
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

    # ── Credential exposure ────────────────────────────────────────────────────

    @property
    def credentials(self):
        return self._credentials
