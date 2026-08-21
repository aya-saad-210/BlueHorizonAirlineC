from pydantic import BaseModel, Field, ConfigDict

from Rag.keyword_index import KeywordIndex
from Rag.vector_store import VectorStore


# ----------------------------------------------------
# Build indexes from the SAME documents used by RAG
# ----------------------------------------------------

vector_store = VectorStore()

knowledge_store = KeywordIndex(
    vector_store.get_all_documents()
)


# ----------------------------------------------------
# Tool schema
# ----------------------------------------------------

class SearchKnowledgeBaseInput(BaseModel):

    query: str = Field(
        ...,
        description="Keywords to search",
    )

    entity_id: str = Field(
        ...,
        description="Flight number",
    )

    top_k: int = Field(
        default=3,
        ge=1,
        le=10,
    )

    model_config = ConfigDict(
        extra="forbid",
    )


# ----------------------------------------------------
# MCP Tool
# ----------------------------------------------------

def search_knowledge_base(
    query: str,
    entity_id: str,
    top_k: int = 3,
) -> str:

    args = SearchKnowledgeBaseInput(
        query=query,
        entity_id=entity_id,
        top_k=top_k,
    )

    matches = knowledge_store.query(
        query_text=args.query,
        top_k=args.top_k,
    )

    # Filter by flight/entity_id using the metadata already
    # stored in the RAG chunks.
    visible = []

    for item in matches:
        metadata = item.get("metadata", {})

        # Support either an explicit entity_id or flight number
        # if one exists in the policy metadata.
        item_entity_id = metadata.get("entity_id")
        flight_number = metadata.get("flight_number")

        if (
            item_entity_id is None
            or item_entity_id == args.entity_id
            or flight_number == args.entity_id
        ):
            visible.append(item)

    if not visible:
        return "No relevant records found for this query."

    results = []

    for item in visible:

        metadata = item.get("metadata", {})

        text = item.get("text", "")

        results.append(
            f"[{metadata.get('source', 'knowledge_base')}] "
            f"{text}"
        )

    return "\n\n".join(results)