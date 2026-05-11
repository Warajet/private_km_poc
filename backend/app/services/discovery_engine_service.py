"""
DiscoveryEngineService — Layer 2 of the 2-layer access-control model.

Responsibilities
────────────────
1. For each DatastoreTarget provided by the routing layer, build and execute
   a Discovery Engine SearchRequest using the caller's impersonated credentials.
2. Enforce the Layer-2 ACL gate: every request carries the impersonated GCP
   identity so Discovery Engine can evaluate acl_info on each document.
3. Apply the JD-code filter expression for CONFIDENTIAL targets.
4. Run all per-target searches in parallel (ThreadPoolExecutor) and merge the
   ranked results.

What this service does NOT do
──────────────────────────────
- It does NOT decide which datastores to query (that is DatastoreRoutingService).
- It does NOT generate the final answer (that is GeminiService).
- It does NOT manage conversation sessions.

Per-bucket ACL setup expected in the datastores
────────────────────────────────────────────────
Bucket        acl_info on each document
────────────  ────────────────────────────────────────────────────────────────
PUBLIC        none (or readers: all)
INTERNAL      readers: [{principals: [{userId: "<dept>@<domain>"}]}]
RELATE        readers: [{principals: [{groupId: "relate-<id>@<domain>"}]}]
CONFIDENTIAL  readers: [{principals: [{userId: "<dept>@<domain>"}]}]
              + structData.required_jd_code: "<JD-CODE>"
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional, Tuple

from google.cloud import discoveryengine_v1 as discoveryengine

from app.config import Settings, get_settings
from app.models.datastore import DatastoreTarget
from app.models.document import DocumentStructData, RetrievedDocument
from app.models.user import AccessLevel, GCPIdentity
from app.utils.gcp_auth import get_impersonated_credentials

logger = logging.getLogger(__name__)

# Maximum documents fetched from each individual datastore per query
_PER_DATASTORE_PAGE_SIZE = 10
# Maximum documents passed on to the Gemini grounding step
_MAX_MERGED_RESULTS = 20


class DiscoveryEngineService:
    """
    Manages search across multiple Discovery Engine datastores for a single
    user identity.  One instance is created per-request by the controller.
    """

    def __init__(
        self,
        gcp_identity: GCPIdentity,
        settings: Optional[Settings] = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._gcp_identity = gcp_identity
        # Credentials are shared across all per-target searches in this request
        self._credentials = get_impersonated_credentials(
            target_email=gcp_identity.impersonated_email,
            scopes=self._settings.dwd_scopes,
            sa_key_file=self._settings.google_application_credentials,
        )
        self._search_client = discoveryengine.SearchServiceClient(
            credentials=self._credentials
        )

    # ── Public API ─────────────────────────────────────────────────────────────

    def search_targets(
        self,
        query: str,
        targets: List[DatastoreTarget],
    ) -> List[RetrievedDocument]:
        """
        Query every DatastoreTarget in parallel and return a merged, relevance-
        ranked list of RetrievedDocuments (capped at _MAX_MERGED_RESULTS).

        Each target is searched with the same impersonated credentials, so
        Discovery Engine ACL sees a consistent identity across all buckets.
        """
        if not targets:
            return []

        all_docs: List[RetrievedDocument] = []

        with ThreadPoolExecutor(max_workers=len(targets)) as pool:
            futures = {
                pool.submit(self._search_single_target, query, target): target
                for target in targets
            }
            for future in as_completed(futures):
                target = futures[future]
                try:
                    docs = future.result()
                    logger.debug(
                        "Bucket %s returned %d docs", target.label, len(docs)
                    )
                    all_docs.extend(docs)
                except Exception as exc:
                    logger.error(
                        "Search failed for bucket %s: %s",
                        target.label,
                        exc,
                        exc_info=True,
                    )

        # Merge: sort by relevance descending, deduplicate by doc id
        seen: set[str] = set()
        merged: List[RetrievedDocument] = []
        for doc in sorted(all_docs, key=lambda d: d.relevance_score, reverse=True):
            if doc.id not in seen:
                seen.add(doc.id)
                merged.append(doc)
            if len(merged) >= _MAX_MERGED_RESULTS:
                break

        logger.info(
            "Multi-datastore search: %d targets → %d unique docs (top %d)",
            len(targets),
            len(merged),
            _MAX_MERGED_RESULTS,
        )
        return merged

    # ── Per-target search ──────────────────────────────────────────────────────

    def _search_single_target(
        self,
        query: str,
        target: DatastoreTarget,
    ) -> List[RetrievedDocument]:
        """Search one datastore bucket and return its documents."""
        serving_config = self._serving_config_path(target.datastore_id)
        filter_expr = self._build_filter(target)

        request = discoveryengine.SearchRequest(
            serving_config=serving_config,
            query=query,
            page_size=_PER_DATASTORE_PAGE_SIZE,
            filter=filter_expr or None,
            # Pass the GCP identity so Discovery Engine can evaluate acl_info
            user_info=discoveryengine.UserInfo(
                user_id=self._gcp_identity.impersonated_email,
            ),
            content_search_spec=discoveryengine.SearchRequest.ContentSearchSpec(
                snippet_spec=discoveryengine.SearchRequest.ContentSearchSpec.SnippetSpec(
                    return_snippet=True,
                    max_snippet_count=2,
                ),
                extractive_content_spec=(
                    discoveryengine.SearchRequest.ContentSearchSpec.ExtractiveContentSpec(
                        max_extractive_answer_count=2,
                        max_extractive_segment_count=3,
                    )
                ),
            ),
            spell_correction_spec=discoveryengine.SearchRequest.SpellCorrectionSpec(
                mode=discoveryengine.SearchRequest.SpellCorrectionSpec.Mode.AUTO,
            ),
        )

        response = self._search_client.search(request=request)
        return self._parse_results(response.results, target)

    # ── Filter construction ────────────────────────────────────────────────────

    def _build_filter(self, target: DatastoreTarget) -> str:
        """
        Build the Discovery Engine filter expression for this target.

        PUBLIC / INTERNAL / RELATE — no additional filter (ACL is enforced by
          the acl_info mechanism; user identity is passed via user_info).

        CONFIDENTIAL — additionally restrict to the user's specific JD code AND
          their department, so a Dept-A user cannot read Dept-B confidential docs
          even if the datastore has mixed content.
        """
        if target.access_level != AccessLevel.CONFIDENTIAL:
            return ""

        dept = self._gcp_identity.department.upper()
        jd = (target.jd_code or "").upper()

        if jd:
            return (
                f'structData.department: ANY("{dept}") AND '
                f'structData.required_jd_code: ANY("{jd}")'
            )
        # No JD code → exclude all confidential docs as a safety net
        return 'structData.required_jd_code: ANY("")'  # matches nothing

    # ── Result parsing ─────────────────────────────────────────────────────────

    def _parse_results(
        self,
        results,
        target: DatastoreTarget,
    ) -> List[RetrievedDocument]:
        docs: List[RetrievedDocument] = []
        for result in results:
            doc = getattr(result, "document", result)
            if doc is None:
                continue

            struct_data = self._parse_struct_data(doc, target.access_level)
            snippet = self._extract_snippet(result, doc)

            docs.append(
                RetrievedDocument(
                    id=doc.id or doc.name,
                    struct_data=struct_data,
                    relevance_score=getattr(result, "relevance_score", 0.0),
                    snippet=snippet,
                    uri=struct_data.source_uri,
                )
            )
        return docs

    def _extract_snippet(self, result, doc) -> str:
        # Prefer extractive answers, then snippets, then nothing
        if hasattr(result, "chunk") and result.chunk:
            return result.chunk.content

        derived = getattr(doc, "derived_struct_data", None)
        if not derived:
            return ""

        snippets_field = derived.get("snippets")
        if snippets_field and snippets_field.list_value.values:
            first = snippets_field.list_value.values[0]
            snippet_val = first.struct_value.fields.get("snippet")
            if snippet_val:
                return snippet_val.string_value

        extractive_answers = derived.get("extractive_answers")
        if extractive_answers and extractive_answers.list_value.values:
            first = extractive_answers.list_value.values[0]
            content_val = first.struct_value.fields.get("content")
            if content_val:
                return content_val.string_value

        return ""

    @staticmethod
    def _parse_struct_data(doc, fallback_level: AccessLevel) -> DocumentStructData:
        sd: dict = {}
        if doc.struct_data:
            for key, val in doc.struct_data.fields.items():
                if val.HasField("string_value"):
                    sd[key] = val.string_value
                elif val.HasField("number_value"):
                    sd[key] = val.number_value
                elif val.HasField("bool_value"):
                    sd[key] = val.bool_value

        raw_level = sd.get("access_level", fallback_level.value).lower()
        try:
            access_level = AccessLevel(raw_level)
        except ValueError:
            access_level = fallback_level

        return DocumentStructData(
            title=sd.get("title", ""),
            content=sd.get("content", ""),
            access_level=access_level,
            department=sd.get("department"),
            relate_id=sd.get("relate_id"),
            required_jd_code=sd.get("required_jd_code"),
            source_uri=sd.get("source_uri"),
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

    # ── Expose credentials for GeminiService ──────────────────────────────────

    @property
    def credentials(self):
        """Expose the impersonated credentials so GeminiService can reuse them."""
        return self._credentials
