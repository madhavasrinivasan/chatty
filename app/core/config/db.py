from tortoise import Tortoise
from app.core.config.config import settings
from lightrag import LightRAG, QueryParam
from lightrag.llm.gemini import gemini_model_complete , gemini_embed
from lightrag.utils import setup_logger, wrap_embedding_func_with_attrs
from yarl import URL
from google import genai
from google.genai import types
from google.genai import errors as genai_errors
import os
import asyncio
import numpy as np

# Embedding model and dimension for LightRAG (google.genai client)
EMBED_MODEL = "gemini-embedding-001"
EMBED_DIM = 768

# Lock to avoid env/global state collision during concurrent initialization
_init_lock = asyncio.Lock()


async def init_db():
    # Tortoise ORM expects 'postgres://' not 'postgresql://'
    db_url = settings.database_url.replace("postgresql://", "postgres://", 1)
    
    await Tortoise.init(
        db_url=db_url,
        modules={"models": ["app.core.models"]},
    )

    if settings.env == "development":
        await Tortoise.generate_schemas(safe=True)


async def close_db():
    await Tortoise.close_connections() 




def _get_project_root() -> str:
    """Project root: directory containing the 'app' package (db.py lives in app/core/config/)."""
    # app/core/config/db.py -> app/core/config -> app/core -> app -> project root
    _here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(_here, "..", "..", ".."))


async def initialize_light_rag(store_id: str) -> LightRAG:
    setup_logger("lightrag", level="INFO")

    # Avoid global state collision: only one init writes env at a time
    async with _init_lock:
        url = URL(settings.database_url)
        os.environ["POSTGRES_USER"] = url.user or ""
        os.environ["POSTGRES_PASSWORD"] = url.password or ""
        os.environ["POSTGRES_HOST"] = url.host or "localhost"
        os.environ["POSTGRES_PORT"] = str(url.port or 5432)
        os.environ["POSTGRES_DATABASE"] = url.path.lstrip("/")

    # RAG working dir: project_root/rag/store_id (ensures predictable path regardless of cwd)
    project_root = _get_project_root()
    work_dir = os.path.join(project_root, "rag", store_id)
    os.makedirs(work_dir, exist_ok=True)

    _embed_client = genai.Client(api_key=settings.gemini_api_key)

    @wrap_embedding_func_with_attrs(
        embedding_dim=EMBED_DIM,
        max_token_size=2048,
        model_name=EMBED_MODEL,
    )
    async def embedding_func(texts: list[str]) -> np.ndarray:
        # 1. Clean inputs to prevent the SDK from choking on empty strings
        safe_texts = [t.strip() if (t and t.strip()) else "---" for t in texts]
        if not safe_texts:
            return np.array([], dtype=np.float32).reshape(0, EMBED_DIM)

        # 2. Call embedding API (async) — same pattern as client.models.embed_content
        response = await _embed_client.aio.models.embed_content(
            model=EMBED_MODEL,
            contents=safe_texts,
            config=types.EmbedContentConfig(output_dimensionality=EMBED_DIM, task_type="RETRIEVAL_DOCUMENT"),
        )
        if not response.embeddings:
            raise RuntimeError("Embedding API returned no embeddings")

        # 3. Build list of vectors (each embedding has .values)
        raw_embeddings = [list(e.values) for e in response.embeddings]

        # 4. Force shape (N, EMBED_DIM): if single text and API returned one flat list, wrap it
        if len(safe_texts) == 1 and len(raw_embeddings) == 1 and isinstance(raw_embeddings[0], (int, float)):
            raw_embeddings = [raw_embeddings]
        elif len(raw_embeddings) != len(safe_texts):
            # Take first N vectors only (API sometimes returns extra)
            raw_embeddings = raw_embeddings[: len(safe_texts)]

        return np.array(raw_embeddings, dtype=np.float32) 

    # @wrap_embedding_func_with_attrs(
    #      embedding_dim=768, max_token_size=2048, model_name="gemini-embedding-001"
    # )
    # async def embedding_func(texts: list[str]) -> np.ndarray:
    #     return await gemini_embed.func(
    #         texts,
    #         api_key=settings.gemini_api_key,
    #         model="gemini-embedding-001",
    #     )


    async def llm_model_func(
        prompt,
        system_prompt=None,
        history_messages=None,
        keyword_extraction=True,
        **kwargs,
    ) -> str:
        return await gemini_model_complete(
            prompt,
            system_prompt=system_prompt,
            history_messages=history_messages,
            api_key=settings.gemini_api_key,
            model_name="gemini-2.0-flash",
            **kwargs
        )

    rag = LightRAG(
        working_dir=work_dir,
        workspace=store_id,
        llm_model_name="gemini-2.0-flash",
        llm_model_func=llm_model_func,
        embedding_func=embedding_func,
        # Performance tuning
        embedding_func_max_async=4,
        embedding_batch_num=8,
        llm_model_max_async=2,

        # Chunking
        chunk_token_size=1200,
        chunk_overlap_token_size=100,
        # PostgreSQL-backed storages
        graph_storage=settings.lightrag_graph_storage       ,
        vector_storage=settings.lightrag_vector_storage,
        doc_status_storage=settings.lightrag_doc_status_storage,
        kv_storage=settings.lightrag_kv_storage,
    )

    await rag.initialize_storages()
    return rag


