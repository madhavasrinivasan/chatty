
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any, Literal
from fastapi import Form
import json


class RegisterRequest(BaseModel):
    name: Optional[str] = None
    username: str
    email: str 
    password: str
    confirm_password: str 
    address: Optional[str] = None
    subscription_id: Optional[int] = None


class LoginRequest(BaseModel):
    email: str
    password: str 


class UploadKnowledgeBaseRequest(BaseModel):
    chatbot_id: Optional[int] = None
    name: str
    urls: Optional[List[str]] = None

    @classmethod
    def as_form(
        cls,
        chatbot_id: Optional[int] = Form(None),
        name: str = Form(...),
        urls: Optional[str] = Form(None), 
    ):
        parsed_urls = None
        if urls:
            # Try to parse as JSON first (if it's a JSON array string)
            try:
                parsed_urls = json.loads(urls)
                if isinstance(parsed_urls, list):
                    # Already a list, use it
                    pass
                else:
                    # Not a list, treat as comma-separated string
                    parsed_urls = [url.strip() for url in urls.split(",") if url.strip()]
            except (json.JSONDecodeError, TypeError):
                # If JSON parsing fails, treat as comma-separated string
                parsed_urls = [url.strip() for url in urls.split(",") if url.strip()]
        
        return cls(
            chatbot_id=chatbot_id,
            name=name,
            urls=parsed_urls,
        ) 


class llmresponse(BaseModel):
     response: str = Field(description="The response from the LLM in markdown format")
     source : List[str]= Field(description="The source of the response") 

class llmrequest(BaseModel):
    question: str = Field(description="The question to ask the LLM")
    store_id: Optional[str] = None  # e.g. "store_126"; if None, uses store_{user.id}
    mode: Optional[str] = Field(default="hybrid", description="Query mode: naive, local, global, hybrid")


class OrchestratorRequest(BaseModel):
    """Request for the AI E-Commerce Orchestrator (IntentRouter + QueryExpander)."""
    message: str = Field(description="Current user message to process.")
    chat_history: Optional[List[Any]] = Field(default_factory=list, description="Recent chat messages for context.")
    pre_fetched_orders: Optional[List[Any] | Dict[str, Any]] = Field(default_factory=dict, description="Pre-fetched order data for context.")
    chatbot_id: Optional[int] = Field(default=None, description="Chatbot/store to use; if set, store_dna is loaded from ecom_store.")
    subscription_plan: Optional[str] = Field(
        default="starter",
        description="Subscription tier for this store/user (e.g. 'starter', 'enterprise').",
    )


class AddshopifyRequest(BaseModel):
    store_name: str


class StoreDNA(BaseModel):
    dna_summary: str = Field(description="A concise summary of the store's niche and style.")
    detected_categories: List[str] = Field(
        description="Top product categories inferred from product titles and About page."
    )


class IntentRoute(BaseModel):
    """Strict JSON output for IntentRouter: which retrieval path to use."""
    route: Literal["ORDER_SUPPORT", "GENERAL_CHAT", "HYBRID_SEARCH", "GRAPH_SEARCH", "PARALLEL_SEARCH"] = Field(
        description="ORDER_SUPPORT: order/tracking/shipping. GENERAL_CHAT: greetings, off-topic. HYBRID_SEARCH: product search, pricing, policies. GRAPH_SEARCH: manual/PDF. PARALLEL_SEARCH: both product and manual."
    )
    extracted_order_number: Optional[str] = Field(
        default=None,
        description="Order number if present (e.g. #1001), otherwise null.",
    )


class SearchPayloadFilters(BaseModel):
    """Optional filters for product search."""
    color: Optional[str] = Field(default=None, description="Extracted color filter or null.")
    size: Optional[str] = Field(default=None, description="Extracted size filter or null.")


class RRFWeights(BaseModel):
    """Reciprocal Rank Fusion weights; must sum to 1.0."""
    keyword_weight: float = Field(ge=0.0, le=1.0, description="Weight for keyword/BM25 search (0.0-1.0).")
    vector_weight: float = Field(ge=0.0, le=1.0, description="Weight for vector/semantic search (0.0-1.0).")


class SearchPayload(BaseModel):
    """Structured search payload for PostgreSQL from QueryExpander."""
    search_keywords: str = Field(
        description="The core product the user is looking for (e.g. 'watch', 'running shoes', 'coffee maker'). Exclude adjectives like 'cheapest' or 'best'."
    )
    semantic_context: str = Field(
        default="",
        description="The full context of the aesthetic or vibe they want. If none, leave blank.",
    )
    sort_column: Optional[Literal["price", "rating", "created_at"]] = Field(
        default=None,
        description="Column to sort by, or null if no clear preference.",
    )
    sort_order: Optional[Literal["ASC", "DESC"]] = Field(
        default=None,
        description="Sort direction, or null.",
    )
    limit: int = Field(default=5, description="How many items to return. Default 5 unless specified.")
    filters: SearchPayloadFilters = Field(
        default_factory=SearchPayloadFilters,
        description="Extracted color/size filters.",
    )
    rrf_weights: RRFWeights = Field(
        default_factory=lambda: RRFWeights(keyword_weight=0.5, vector_weight=0.5),
        description="Weights for RRF: keyword_weight and vector_weight (must sum to 1.0).",
    )


# --- LLM synthesis & frontend response (strict data contracts) ---


class IntentToCart(BaseModel):
    """Product selected by the LLM for the user. Use empty list for requested_options if no specific variant."""
    product_id: str = Field(
        description="e.g. gid://shopify/Product/123456789 or local store_knowledge id."
    )
    requested_options: List[str] = Field(
        default_factory=list,
        description="Variant options requested, e.g. ['Black', 'XL'] or ['Cotton', 'Slim Fit']. Empty [] if no specific variant.",
    )


class LLMSynthesisOutput(BaseModel):
    """Schema forced on the LLM. Leave lists empty [] when not relevant to the user's query."""
    general_answer: str = Field(
        description="Answer in Markdown. Use safe phrasing when recommending products."
    )
    urls: List[str] = Field(
        default_factory=list,
        description="Links to policies, sizing guides, or collection pages. Empty [] if none."
    )
    selected_products: List[IntentToCart] = Field(
        default_factory=list,
        description="Products to show. Empty [] if the query is not product-related."
    )
    suggested_actions: List[str] = Field(
        default_factory=list,
        description="2-3 short follow-up questions for the UI."
    )


class FrontendProductCard(BaseModel):
    """Hydrated product card for the React frontend."""
    product_id: str = Field(description="Product identifier (GID or DB id).")
    variant_id: str = Field(description="Resolved variant identifier.")
    title: str = Field(description="Product title.")
    price: str = Field(description="Display price string.")
    currency: str = Field(description="Currency code, e.g. USD.")
    image_url: str = Field(default="", description="Primary image URL.")
    handle: str = Field(description="Product handle for URL.")
    in_stock: bool = Field(description="True if variant is in stock.")


class FinalFrontendResponse(BaseModel):
    """Final JSON sent to the frontend after synthesis and hydration."""
    general_answer: str = Field(description="Markdown answer, possibly updated for all-OOS.")
    urls: List[str] = Field(default_factory=list, description="Policy/collection URLs.")
    products: List[FrontendProductCard] = Field(
        default_factory=list,
        description="Hydrated product cards (only in-stock items).",
    )
    suggested_actions: List[str] = Field(
        default_factory=list,
        description="2-3 suggested follow-up questions.",
    )