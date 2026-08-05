"""
DatabaseExecutor: runs search against store_knowledge using QueryExpander payload.
- Branch A: Catalog browse — no context & no keywords; raw SQL, no embedding.
- Branch B: Keyword only — no context, has keywords; websearch_to_tsquery, no embedding.
- Branch C: Full hybrid — has semantic_context; embedding API + RRF with DISTINCT ON.
"""
from __future__ import annotations

import json
from typing import Any

from tortoise import connections

from app.core.services.webcrawler import Services


# Store knowledge types we search (collection stored as 'collect' in DB)
SEARCH_DATA_TYPES = ("product", "page", "collect", "custom")
_SEARCH_TYPES_SQL = "'product', 'page', 'collect', 'custom'"


def _build_catalog_browse_sql(
    store_id: int,
    sort_column: str | None,
    sort_order: str | None,
    limit: int,
    filters: dict | None = None,
) -> tuple[str, list[Any]]:
    """Branch A: No context, no keywords. Simple catalog browse (e.g. 'What is the cheapest product?')."""
    order_col = "price"
    order_dir = "ASC"
    if sort_column in ("price", "created_at", "rating"):
        order_col = sort_column if sort_column != "rating" else "created_at"
    if sort_order and sort_order.upper() in ("ASC", "DESC"):
        order_dir = sort_order.upper()

    params: list[Any] = [store_id]
    pos = 2
    filter_clauses: list[str] = []
    if filters:
        if filters.get("color"):
            filter_clauses.append(
                f" AND (variant_data IS NOT NULL AND (variant_data->>'color')::text ILIKE ${pos}) "
            )
            params.append(f"%{filters['color']}%")
            pos += 1
        if filters.get("size"):
            filter_clauses.append(
                f" AND (variant_data IS NOT NULL AND (variant_data->>'size')::text ILIKE ${pos}) "
            )
            params.append(f"%{filters['size']}%")
            pos += 1
        if filters.get("category"):
            # Category lives in content / title for Comez+Shopify synced rows
            filter_clauses.append(
                f" AND (title ILIKE ${pos} OR coalesce(content, '') ILIKE ${pos}) "
            )
            params.append(f"%{filters['category']}%")
            pos += 1
    filter_sql = "".join(filter_clauses)
    params.append(limit)
    limit_pos = len(params)

    # Numeric price sort so "10" < "100" (text sort would be wrong)
    if order_col == "price":
        order_sql = f"ORDER BY NULLIF(regexp_replace(coalesce(price::text, ''), '[^0-9.\\-]', '', 'g'), '')::numeric {order_dir} NULLS LAST"
    else:
        order_sql = f"ORDER BY {order_col} {order_dir} NULLS LAST"

    sql = f"""
    SELECT id, title, content, price, url, image_url
    FROM store_knowledge
    WHERE store_id = $1
      AND data_type = 'product'
      {filter_sql}
    {order_sql}
    LIMIT ${limit_pos}
    """
    return sql, params


def _append_common_filters(
    filters: dict | None,
    params: list[Any],
    pos: int,
) -> tuple[str, int]:
    """Shared color/size/category ILIKE clauses. Returns (sql_fragment, next_pos)."""
    filter_clauses: list[str] = []
    if not filters:
        return "", pos
    if filters.get("color"):
        filter_clauses.append(
            f" AND (variant_data IS NOT NULL AND (variant_data->>'color')::text ILIKE ${pos}) "
        )
        params.append(f"%{filters['color']}%")
        pos += 1
    if filters.get("size"):
        filter_clauses.append(
            f" AND (variant_data IS NOT NULL AND (variant_data->>'size')::text ILIKE ${pos}) "
        )
        params.append(f"%{filters['size']}%")
        pos += 1
    if filters.get("category"):
        filter_clauses.append(
            f" AND (title ILIKE ${pos} OR coalesce(content, '') ILIKE ${pos}) "
        )
        params.append(f"%{filters['category']}%")
        pos += 1
    return "".join(filter_clauses), pos


def _price_order_sql(sort_order: str) -> str:
    return (
        "ORDER BY NULLIF(regexp_replace(coalesce(price::text, ''), '[^0-9.\\-]', '', 'g'), '')::numeric "
        f"{sort_order} NULLS LAST"
    )


# Generic shopper words that often appear in queries but rarely in product titles.
_KEYWORD_STOPWORDS = frozenset(
    {
        "a", "an", "the", "and", "or", "for", "with", "in", "on", "of", "to",
        "do", "u", "you", "have", "has", "any", "some", "show", "me", "find",
        "get", "want", "need", "looking", "please", "can", "could", "ahve",
        "clothes", "clothing", "items", "item", "products", "product", "stuff",
        "things", "apparel", "wear",
    }
)


def _loosen_keywords(search_keywords: str) -> str | None:
    """
    Turn 'muslin clothes' into 'muslin' (or 'muslin OR jabla') so full-text AND
    doesn't require every shopper word to appear in the product text.
    """
    raw = (search_keywords or "").strip()
    if not raw or " OR " in raw.upper():
        return None
    tokens = [t for t in raw.replace("|", " ").split() if t]
    kept = [t for t in tokens if t.lower().strip(".,!?") not in _KEYWORD_STOPWORDS]
    if not kept or " ".join(kept).lower() == raw.lower():
        # If nothing dropped, try OR between tokens when 2+
        if len(tokens) >= 2:
            return " OR ".join(tokens)
        return None
    if len(kept) == 1:
        return kept[0]
    return " OR ".join(kept)


def _build_keyword_only_sql(
    store_id: int,
    search_keywords: str,
    filters: dict | None,
    sort_column: str | None,
    sort_order: str | None,
    limit: int,
) -> tuple[str, list[Any]]:
    """Branch B: No context, has keywords. Keyword-only full-text with websearch_to_tsquery."""
    params: list[Any] = [store_id, search_keywords.strip()]
    pos = 3
    filter_sql, pos = _append_common_filters(filters, params, pos)
    params.append(limit)
    limit_pos = len(params)

    if sort_column in ("price", "created_at") and sort_order and sort_order.upper() in ("ASC", "DESC"):
        order_dir = sort_order.upper()
        if sort_column == "price":
            order_sql = _price_order_sql(order_dir)
        else:
            order_sql = f"ORDER BY {sort_column} {order_dir} NULLS LAST"
    else:
        order_sql = "ORDER BY keyword_score DESC"

    # websearch_to_tsquery understands Google-style OR, quotes, etc.; ts_rank gives keyword_score
    sql = f"""
    SELECT id, title, content, price, url, image_url,
           ts_rank(to_tsvector('english', title || ' ' || coalesce(content, '')), websearch_to_tsquery('english', $2)) AS keyword_score
    FROM store_knowledge
    WHERE store_id = $1
      AND data_type = 'product'
      AND to_tsvector('english', title || ' ' || coalesce(content, '')) @@ websearch_to_tsquery('english', $2)
      {filter_sql}
    {order_sql}
    LIMIT ${limit_pos}
    """
    return sql, params


def _build_path_a_sql_explicit(
    store_id: int,
    search_keywords: str,
    filters: dict | None,
    sort_column: str | None,
    sort_order: str | None,
    limit: int,
) -> tuple[str, list[Any]]:
    params: list[Any] = [store_id]
    pos = 2
    filter_sql, pos = _append_common_filters(filters, params, pos)

    keyword_sql = ""
    if search_keywords and search_keywords.strip():
        keyword_sql = f" AND to_tsvector('english', title || ' ' || coalesce(content, '')) @@ plainto_tsquery('english', ${pos}) "
        params.append(search_keywords.strip())
        pos += 1
    limit_pos = pos
    params.append(limit)

    order_col = "created_at"
    order_dir = "DESC"
    if sort_column in ("price", "created_at", "rating"):
        order_col = sort_column
    if sort_order and sort_order.upper() in ("ASC", "DESC"):
        order_dir = sort_order.upper()
    if order_col == "rating":
        order_col = "created_at"

    if order_col == "price":
        order_sql = _price_order_sql(order_dir)
    else:
        order_sql = f"ORDER BY {order_col} {order_dir} NULLS LAST"

    sql = f"""
    SELECT id, title, content, price, url, image_url
    FROM store_knowledge
    WHERE store_id = $1
      AND data_type IN ('product', 'page', 'collect', 'custom')
      {keyword_sql}
      {filter_sql}
    {order_sql}
    LIMIT ${limit_pos}
    """
    return sql, params


def _build_path_b_sql(
    store_id: int,
    vector_json: str,
    search_keywords: str,
    vector_weight: float,
    keyword_weight: float,
    filters: dict | None,
    sort_column: str | None,
    sort_order: str | None,
    limit: int,
) -> tuple[str, list[Any]]:
    """Path B: RRF hybrid with dynamic sorting."""
    params: list[Any] = [store_id, vector_json]
    pos = 3
    filter_sql, pos = _append_common_filters(filters, params, pos)

    keyword_param_pos = pos if (search_keywords and search_keywords.strip()) else None
    if keyword_param_pos is not None:
        params.append(search_keywords.strip())
        pos += 1
        
    vector_w_pos = pos
    params.append(vector_weight)
    pos += 1
    
    keyword_w_pos = pos
    params.append(keyword_weight)
    pos += 1
    
    limit_pos = pos
    params.append(limit)

    # Keyword CTE: websearch_to_tsquery for Google-style OR syntax
    if keyword_param_pos is not None:
        keyword_cte = f"""
    keyword_search AS (
        SELECT id, title, content, price, url, image_url,
               ROW_NUMBER() OVER (ORDER BY ts_rank(to_tsvector('english', title || ' ' || coalesce(content, '')), websearch_to_tsquery('english', ${keyword_param_pos})) DESC) as rank_k
        FROM store_knowledge
        WHERE store_id = $1
          AND data_type IN ('product', 'page', 'collect', 'custom')
          AND to_tsvector('english', title || ' ' || coalesce(content, '')) @@ websearch_to_tsquery('english', ${keyword_param_pos})
          {filter_sql}
        LIMIT 40
    )"""
    else:
        keyword_cte = """
    keyword_search AS (
        SELECT id, title, content, price, url, image_url, 0::integer as rank_k
        FROM store_knowledge
        WHERE 1 = 0
    )"""

    final_score_expr = f"( COALESCE(${vector_w_pos} * (1.0 / (60 + v.rank_v)), 0.0) + COALESCE(${keyword_w_pos} * (1.0 / (60 + k.rank_k)), 0.0) )"

    # --- NEW DYNAMIC SORTING LOGIC ---
    if sort_column == "price":
        # Cast price to numeric for correct mathematical sorting, default to ASC if not specified
        s_order = sort_order if sort_order in ["ASC", "DESC"] else "ASC"
        # We fall back to final_score for tie-breakers
        final_order_by = f"CAST(COALESCE(v.price, k.price) AS NUMERIC) {s_order}, {final_score_expr} DESC"
        
    elif sort_column in ["rating", "created_at"]:
        # If you add these columns later, sort them here
        s_order = sort_order if sort_order in ["ASC", "DESC"] else "DESC"
        final_order_by = f"COALESCE(v.{sort_column}, k.{sort_column}) {s_order}, {final_score_expr} DESC"
        
    else:
        # Default fallback: strictly sort by hybrid relevance score
        final_order_by = f"{final_score_expr} DESC, COALESCE(v.id, k.id)"
    # ---------------------------------

    sql = f"""
    WITH
    vector_search AS (
        SELECT id, title, content, price, url, image_url,
               ROW_NUMBER() OVER (ORDER BY embedding <=> $2::vector) as rank_v
        FROM store_knowledge
        WHERE store_id = $1
          AND data_type IN ('product', 'page', 'collect', 'custom')
          AND embedding IS NOT NULL
          {filter_sql}
        ORDER BY embedding <=> $2::vector
        LIMIT 40
    ),
    {keyword_cte}
    SELECT
        COALESCE(v.id, k.id) as id,
        COALESCE(v.title, k.title) as title,
        COALESCE(v.content, k.content) as content,
        COALESCE(v.price, k.price) as price,
        COALESCE(v.url, k.url) as url,
        COALESCE(v.image_url, k.image_url) as image_url,
        {final_score_expr} as final_score
    FROM vector_search v
    FULL OUTER JOIN keyword_search k ON v.id = k.id
    ORDER BY {final_order_by}
    LIMIT ${limit_pos}
    """
    
    return sql, params

class DatabaseExecutor:
    """Executes search against store_knowledge from QueryExpander payload."""

    @staticmethod
    async def execute_search(store_id: int, payload: dict) -> list[dict[str, Any]]:
        """
        Strict branching: Branch A (catalog browse) no context & no keywords; Branch B (keyword only) no context but has keywords; Branch C (full hybrid) has context.
        Returns list of dicts with keys id, title, content, price, url, image_url (and final_score for Branch C).
        """
        payload = payload or {}
        filters_raw = payload.get("filters") or {}
        if isinstance(filters_raw, dict):
            filters = {
                "color": filters_raw.get("color"),
                "size": filters_raw.get("size"),
                "category": filters_raw.get("category"),
            }
        else:
            filters = {}
        search_keywords = (payload.get("search_keywords") or "").strip()
        semantic_context = (payload.get("semantic_context") or "").strip()
        sort_column = payload.get("sort_column")
        sort_order = payload.get("sort_order")
        limit = int(payload.get("limit") or 5)
        limit = max(1, min(limit, 50))

        # "Cheapest X" is SQL sort, not vector search — drop semantic so Branch B can price-sort.
        if (sort_column or "").lower() == "price" and search_keywords:
            semantic_context = ""

        conn = connections.get("default")

        # Branch A: Catalog browse — no keywords (e.g. 'What is the cheapest product?')
        if not search_keywords:
            try:
                sql, params = _build_catalog_browse_sql(
                    store_id, sort_column, sort_order, limit, filters=filters
                )
                rows = await conn.execute_query_dict(sql, params)
                out = [dict(r) for r in rows] if rows else []
                if not out and filters.get("category"):
                    sql, params = _build_catalog_browse_sql(
                        store_id,
                        sort_column,
                        sort_order,
                        limit,
                        filters={**filters, "category": None},
                    )
                    rows = await conn.execute_query_dict(sql, params)
                    out = [dict(r) for r in rows] if rows else []
                    print(
                        f"DatabaseExecutor Branch A retry without category → {len(out)} rows",
                        flush=True,
                    )
                print(
                    f"DatabaseExecutor Branch A (browse): sort={sort_column}/{sort_order} "
                    f"filters={filters} → {len(out)} rows",
                    flush=True,
                )
                return out
            except Exception as e:
                print(f"DatabaseExecutor Branch A (catalog) error: {e}", flush=True)
                return []

        async def _run_keyword(kw: str, filt: dict) -> list[dict[str, Any]]:
            sql, params = _build_keyword_only_sql(
                store_id, kw, filt, sort_column, sort_order, limit
            )
            rows = await conn.execute_query_dict(sql, params)
            return [dict(r) for r in rows] if rows else []

        # Branch B: Keyword only — no semantic_context. No embedding API.
        if not semantic_context and search_keywords:
            try:
                rows = await _run_keyword(search_keywords, filters)
                print(
                    f"DatabaseExecutor Branch B (keyword): kw={search_keywords!r} "
                    f"filters={filters} sort={sort_column}/{sort_order} → {len(rows)} rows",
                    flush=True,
                )
                # Soft category: LLM often invents 'apparel' etc. that never appear in catalog text.
                if not rows and filters.get("category"):
                    soft = {**filters, "category": None}
                    rows = await _run_keyword(search_keywords, soft)
                    print(
                        f"DatabaseExecutor Branch B retry without category={filters.get('category')!r} "
                        f"→ {len(rows)} rows",
                        flush=True,
                    )
                # Loosen AND keywords: 'muslin clothes' → 'muslin'
                if not rows:
                    loose = _loosen_keywords(search_keywords)
                    if loose:
                        soft = {**filters, "category": None}
                        rows = await _run_keyword(loose, soft)
                        print(
                            f"DatabaseExecutor Branch B loosened kw={loose!r} → {len(rows)} rows",
                            flush=True,
                        )
                return rows
            except Exception as e:
                print(f"DatabaseExecutor Branch B (keyword) error: {e}", flush=True)
                return []

        # Branch C: Full hybrid — has semantic_context. Call embedding API then RRF with DISTINCT ON.
        try:
            embedding = await Services.generate_embedding(semantic_context)
            if embedding is not None and not isinstance(embedding, list):
                embedding = list(embedding)
            vector_json = json.dumps(embedding or [])
        except Exception as e:
            print(f"DatabaseExecutor embedding error: {e}", flush=True)
            return []
        rrf = payload.get("rrf_weights") or {}
        vector_weight = float(rrf.get("vector_weight", 0.5))
        keyword_weight = float(rrf.get("keyword_weight", 0.5))
        total = vector_weight + keyword_weight
        if total <= 0:
            vector_weight, keyword_weight = 0.5, 0.5
        else:
            vector_weight /= total
            keyword_weight /= total

        async def _run_hybrid(kw: str, filt: dict) -> list[dict[str, Any]]:
            sql, params = _build_path_b_sql(
                store_id,
                vector_json,
                kw,
                vector_weight,
                keyword_weight,
                filt,
                sort_column,
                sort_order,
                limit,
            )
            rows = await conn.execute_query_dict(sql, params)
            return [dict(r) for r in rows] if rows else []

        try:
            rows = await _run_hybrid(search_keywords, filters)
            if not rows and filters.get("category"):
                rows = await _run_hybrid(search_keywords, {**filters, "category": None})
                print(
                    f"DatabaseExecutor Branch C retry without category → {len(rows)} rows",
                    flush=True,
                )
            if not rows:
                loose = _loosen_keywords(search_keywords)
                if loose:
                    rows = await _run_hybrid(loose, {**filters, "category": None})
                    print(
                        f"DatabaseExecutor Branch C loosened kw={loose!r} → {len(rows)} rows",
                        flush=True,
                    )
            return rows
        except Exception as e:
            print(f"DatabaseExecutor Branch C (hybrid) error: {e}", flush=True)
            return []


async def execute_search(store_id: int, payload: dict) -> list[dict[str, Any]]:
    """Convenience async function: run DatabaseExecutor.execute_search."""
    return await DatabaseExecutor.execute_search(store_id, payload)
