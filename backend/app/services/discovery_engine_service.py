"""
DiscoveryEngineService — uses ConversationalSearchServiceClient.answer_query()

This is the correct Gemini Enterprise pattern from the Discovery Engine API.
One answer_query call per accessible datastore target combines:
  - Document retrieval (with ACL enforcement via impersonated credentials)
  - Answer generation   (Gemini grounded on the retrieved documents)

There is NO separate GeminiService. answer_query() handles both steps in a
single API call.

Multi-turn session management
──────────────────────────────
Discovery Engine sessions are per-datastore. For a chat session that spans
N accessible datastores, we maintain N DE sessions (one per datastore_id).

Turn 1 – no existing DE session for a target:
  Pass session = auto_session_path(datastore_id)  →  DE creates a new session
  Response carries the real session resource name  →  store in de_sessions dict

Turn 2+ – existing DE session:
  Pass session = de_sessions[datastore_id]         →  DE continues conversation
  Response updates the session resource name       →  update de_sessions dict

If a new bucket becomes accessible (e.g. user joins a relate group), a new
session is auto-created for that bucket on first use.

ACL enforcement
───────────────
Every answer_query request is made with the caller's impersonated credentials
(domain-wide delegation). Discovery Engine evaluates acl_info on each document
against the calling identity automatically.

For CONFIDENTIAL targets an additional filter expression is embedded in the
SearchSpec to restrict results to documents matching the user's JD code.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from google.cloud import discoveryengine_v1 as discoveryengine

from app.config import Settings, get_settings
from app.models.datastore import DatastoreTarget
from app.models.document import DocumentStructData, RetrievedDocument
from app.models.user import AccessLevel, GCPIdentity
from app.utils.gcp_auth import get_impersonated_credentials

logger = logging.getLogger(__name__)


@dataclass
class TargetAnswer:
    """Answer produced by a single answer_query call for one datastore target."""
    datastore_id: str
    label: str
    answer_text: str
    references: List[RetrievedDocument] = field(default_factory=list)
    # Updated session resource name returned by DE (pass on next turn)
    session_name: str = ""
    grounded: bool = False


@dataclass
class AggregatedAnswer:
    """Merged result from all accessible datastore targets."""
    answer_text: str
    references: List[RetrievedDocument]
    # Updated de_sessions to persist on the ChatSession after this turn
    updated_de_sessions: Dict[str, str]
    grounded: bool = False


class DiscoveryEngineService:
    """
    Wraps ConversationalSearchServiceClient.answer_query() for multi-datastore
    access-controlled grounded search.

    One instance is created per-request by the controller, scoped to the
    caller's impersonated GCP credentials.
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

    def answer_all_targets(
        self,
        query: str,
        targets: List[DatastoreTarget],
        de_sessions: Dict[str, str],
    ) -> AggregatedAnswer:
        """
        Call answer_query() for every accessible datastore target in parallel.

        de_sessions  – existing DE session resource names keyed by datastore_id.
                       Empty dict on first turn; populated from prior response.

        Returns an AggregatedAnswer with:
          - answer_text:        best single answer (most grounded) or
                                combined when multiple datastores contribute
          - references:         deduplicated union of all target references
          - updated_de_sessions: new session names to store on the ChatSession
        """
        if not targets:
            return AggregatedAnswer(
                answer_text="",
                references=[],
                updated_de_sessions={},
            )

        target_answers: List[TargetAnswer] = []

        with ThreadPoolExecutor(max_workers=len(targets)) as pool:
            futures = {
                pool.submit(
                    self._answer_single_target,
                    query,
                    target,
                    de_sessions.get(target.datastore_id, ""),
                ): target
                for target in targets
            }
            for future in as_completed(futures):
                target = futures[future]
                try:
                    ta = future.result()
                    target_answers.append(ta)
                    logger.debug(
                        "Bucket %s → %d refs, session=%s",
                        ta.label,
                        len(ta.references),
                        ta.session_name,
                    )
                except Exception as exc:
                    logger.error(
                        "answer_query failed for bucket %s: %s",
                        target.label,
                        exc,
                        exc_info=True,
                    )

        return self._aggregate(target_answers)

    # ── Per-target answer_query call ───────────────────────────────────────────

    def _answer_single_target(
        self,
        query: str,
        target: DatastoreTarget,
        existing_session: str,
    ) -> TargetAnswer:
        """
        Execute one answer_query() call for a single DatastoreTarget.

        existing_session  – "" for first turn (DE auto-creates a new session)
                            resource-name string to continue an existing session
        """
        serving_config = self._serving_config_path(target.datastore_id)
        session = self._resolve_session(target.datastore_id, existing_session)

        request = discoveryengine.AnswerQueryRequest(
            serving_config=serving_config,
            # Core query
            query=discoveryengine.Query(text=query),
            # Session: auto-create on first turn, continue on subsequent turns
            session=session,
            # Stable pseudo-id for session tracking / analytics
            user_pseudo_id=self._gcp_identity.impersonated_email,
            # Query understanding: rephrase + classify
            query_understanding_spec=self._build_query_understanding_spec(),
            # Answer generation: model + system prompt + citations
            answer_generation_spec=self._build_answer_generation_spec(),
            # Search: document count + JD-code filter for CONFIDENTIAL
            search_spec=self._build_search_spec(target),
            # Related questions (optional UX enhancement)
            related_questions_spec=discoveryengine.AnswerQueryRequest.RelatedQuestionsSpec(
                enable=False,
            ),
        )

        response = self._client.answer_query(request=request)

        answer_text = ""
        if response.answer and response.answer.answer_text:
            answer_text = response.answer.answer_text

        references = self._extract_references(response)
        new_session = response.session or session

        return TargetAnswer(
            datastore_id=target.datastore_id,
            label=target.label,
            answer_text=answer_text,
            references=references,
            session_name=new_session,
            grounded=bool(references),
        )

    # ── Request builders ───────────────────────────────────────────────────────

    def _build_query_understanding_spec(
        self,
    ) -> discoveryengine.AnswerQueryRequest.QueryUnderstandingSpec:
        return discoveryengine.AnswerQueryRequest.QueryUnderstandingSpec(
            query_rephraser_spec=discoveryengine.AnswerQueryRequest.QueryUnderstandingSpec.QueryRephraserSpec(
                disable=False,
                max_rephrase_steps=1,
            ),
            query_classification_spec=discoveryengine.AnswerQueryRequest.QueryUnderstandingSpec.QueryClassificationSpec(
                types=[
                    discoveryengine.AnswerQueryRequest.QueryUnderstandingSpec.QueryClassificationSpec.Type.ADVERSARIAL_QUERY,
                    discoveryengine.AnswerQueryRequest.QueryUnderstandingSpec.QueryClassificationSpec.Type.NON_ANSWER_SEEKING_QUERY,
                ]
            ),
        )

    def _build_answer_generation_spec(
        self,
    ) -> discoveryengine.AnswerQueryRequest.AnswerGenerationSpec:
        return discoveryengine.AnswerQueryRequest.AnswerGenerationSpec(
            model_spec=discoveryengine.AnswerQueryRequest.AnswerGenerationSpec.ModelSpec(
                model_version=self._settings.gemini_model,
            ),
            prompt_spec=discoveryengine.AnswerQueryRequest.AnswerGenerationSpec.PromptSpec(
                preamble=self._settings.gemini_system_prompt,
            ),
            include_citations=True,
            answer_language_code="en",
            ignore_adversarial_query=True,
            ignore_non_answer_seeking_query=True,
        )

    def _build_search_spec(
        self,
        target: DatastoreTarget,
    ) -> discoveryengine.AnswerQueryRequest.SearchSpec:
        filter_expr = self._build_filter(target)
        params = discoveryengine.AnswerQueryRequest.SearchSpec.SearchParams(
            max_return_results=10,
        )
        if filter_expr:
            params.filter = filter_expr
        return discoveryengine.AnswerQueryRequest.SearchSpec(search_params=params)

    def _build_filter(self, target: DatastoreTarget) -> str:
        """
        Confidential targets require a dept + JD-code filter so that a user
        cannot read another department's confidential docs even if they are in
        the same confidential datastore.

        All other bucket types rely entirely on Discovery Engine ACL (acl_info).
        """
        if target.access_level != AccessLevel.CONFIDENTIAL:
            return ""

        dept = self._gcp_identity.department.upper()
        jd = (target.jd_code or "").upper()
        if not jd:
            # Safety net: no JD code → match nothing in confidential bucket
            return 'structData.required_jd_code: ANY("__never__")'

        return (
            f'structData.department: ANY("{dept}") AND '
            f'structData.required_jd_code: ANY("{jd}")'
        )

    # ── Session helpers ────────────────────────────────────────────────────────

    def _resolve_session(self, datastore_id: str, existing_session: str) -> str:
        """
        Return the session value to pass to answer_query.

        If no existing session → auto-create path (.../sessions/-)
        If existing session resource name → pass it directly to continue the turn
        """
        if existing_session:
            return existing_session
        # Auto-create: Discovery Engine will create a new session and return its name
        return (
            f"projects/{self._settings.gcp_project_id}"
            f"/locations/{self._settings.gcp_location}"
            f"/collections/default_collection"
            f"/dataStores/{datastore_id}"
            f"/sessions/-"
        )

    # ── Reference extraction ───────────────────────────────────────────────────

    def _extract_references(
        self,
        response: discoveryengine.AnswerQueryResponse,
    ) -> List[RetrievedDocument]:
        """
        Parse Answer.references into RetrievedDocument objects.

        The DE API returns references in one of two shapes:
          - chunk_info         (chunked datastores)
          - unstructured_document_info  (unstructured/HTML datastores)
        """
        if not response.answer or not response.answer.references:
            return []

        docs: List[RetrievedDocument] = []
        seen_docs: set[str] = set()  # deduplicate by document resource name

        for ref in response.answer.references:
            doc = self._parse_reference(ref)
            if doc and doc.id not in seen_docs:
                seen_docs.add(doc.id)
                docs.append(doc)

        return docs

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
                struct_data = self._parse_struct_data_from_proto(
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
                snippet = ""
                score = 0.0
                if udi.chunk_contents:
                    best = max(
                        udi.chunk_contents,
                        key=lambda c: c.relevance_score or 0.0,
                        default=udi.chunk_contents[0],
                    )
                    snippet = best.content
                    score = best.relevance_score or 0.0

                struct_data = self._parse_struct_data_from_proto(
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
    def _parse_struct_data_from_proto(struct_proto) -> DocumentStructData:
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

    # ── Answer aggregation ─────────────────────────────────────────────────────

    def _aggregate(self, target_answers: List[TargetAnswer]) -> AggregatedAnswer:
        """
        Merge answers from all targets into a single AggregatedAnswer.

        Strategy
        ─────────
        1. Collect updated session names for every target (first turn or
           continued turn).
        2. Identify targets that returned a grounded answer (non-empty text
           AND at least one reference).
        3. Pick the primary answer:
           - From the grounded target with the most references.
           - If no grounded answer exists, use the first non-empty text.
           - Fall back to empty string.
        4. Merge all reference lists, deduplicating by document id, sorted by
           relevance score descending.
        """
        updated_sessions: Dict[str, str] = {}
        all_refs: List[RetrievedDocument] = []
        seen_doc_ids: set[str] = set()

        grounded_answers = [ta for ta in target_answers if ta.grounded and ta.answer_text]
        ungrounded_answers = [ta for ta in target_answers if ta.answer_text and not ta.grounded]

        for ta in target_answers:
            if ta.session_name:
                updated_sessions[ta.datastore_id] = ta.session_name
            for doc in ta.references:
                if doc.id not in seen_doc_ids:
                    seen_doc_ids.add(doc.id)
                    all_refs.append(doc)

        # Sort merged references by relevance descending
        all_refs.sort(key=lambda d: d.relevance_score, reverse=True)

        # Select primary answer text
        if grounded_answers:
            # Use the answer backed by the most documents
            primary = max(grounded_answers, key=lambda ta: len(ta.references))
            answer_text = primary.answer_text
        elif ungrounded_answers:
            answer_text = ungrounded_answers[0].answer_text
        else:
            answer_text = ""

        logger.info(
            "Aggregated: %d targets → %d grounded, %d unique refs, answer_len=%d",
            len(target_answers),
            len(grounded_answers),
            len(all_refs),
            len(answer_text),
        )

        return AggregatedAnswer(
            answer_text=answer_text,
            references=all_refs[:20],  # cap at 20 to keep response size sane
            updated_de_sessions=updated_sessions,
            grounded=bool(grounded_answers),
        )

    # ── Path helpers ───────────────────────────────────────────────────────────

    def _serving_config_path(self, datastore_id: str) -> str:
        return (
            f"projects/{self._settings.gcp_project_id}"
            f"/locations/{self._settings.gcp_location}"
            f"/collections/default_collection"
            f"/dataStores/{datastore_id}"
            f"/servingConfigs/{self._settings.discovery_engine_serving_config_id}"
        )

    # ── Expose credentials for reuse ───────────────────────────────────────────

    @property
    def credentials(self):
        return self._credentials
