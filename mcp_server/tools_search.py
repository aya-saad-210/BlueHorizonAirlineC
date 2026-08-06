from pydantic import BaseModel, Field, ConfigDict
from keyword_search import KeywordStore

knowledge_store = KeywordStore()


# ----------------------------------------------------
# Index documents (run once)
# ----------------------------------------------------
def index_documents():

    knowledge_store.upsert(
        payload="Flight BH202 was disrupted because of a mechanical issue.",
        metadata={
            "entity_id": "BH202",
            "role_required": "any"
        }
    )

    knowledge_store.upsert(
        payload="Passengers on BH202 may receive compensation depending on airline policy.",
        metadata={
            "entity_id": "BH202",
            "role_required": "any"
        }
    )

    knowledge_store.upsert(
        payload="Reserve crew assignment for BH202 requires supervisor approval.",
        metadata={
            "entity_id": "BH202",
            "role_required": "supervisor"
        }
    )

    knowledge_store.upsert(
        payload="Flight BH303 was cancelled due to severe weather.",
        metadata={
            "entity_id": "BH303",
            "role_required": "any"
        }
    )


index_documents()


# ----------------------------------------------------
# Tool schema
# ----------------------------------------------------
class SearchKnowledgeBaseInput(BaseModel):

    query: str = Field(
        ...,
        description="Keywords to search"
    )

    entity_id: str = Field(
        ...,
        description="Flight number"
    )

    top_k: int = Field(
        default=3,
        ge=1,
        le=10
    )

    model_config = ConfigDict(
        extra="forbid"
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

    session_role = "any"

    matches = knowledge_store.query(
        query_text=args.query,
        top_k=args.top_k,
        filter={
            "entity_id": args.entity_id
        }
    )

    visible = [
        item
        for item in matches
        if item["metadata"]["role_required"] in (
            "any",
            session_role,
        )
    ]

    if not visible:
        return "No relevant records found for this query."

    return "\n\n".join(
        str(item["payload"])
        for item in visible
    )