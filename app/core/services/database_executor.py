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
SEARCH_DATA_TYPES = ("product", "page", "collect")


def _build_catalog_browse_sql(
    store_id: int,
    sort_column: str | None,
    sort_order: str | None,
    limit: int,
) -> tuple[str, list[Any]]:
    """Branch A: No context, no keywords. Simple catalog browse (e.g. 'What is the cheapest product?')."""
    order_col = "price"
    order_dir = "ASC"
    if sort_column in ("price", "created_at", "rating"):
        order_col = sort_column if sort_column != "rating" else "created_at"
    if sort_order and sort_order.upper() in ("ASC", "DESC"):
        order_dir = sort_order.upper()
    sql = f"""
    SELECT id, title, content, price, url, image_url
    FROM store_knowledge
    WHERE store_id = $1
      AND data_type IN ('product', 'collect')
    ORDER BY {order_col} {order_dir} NULLS LAST
    LIMIT $2
    """
    return sql, [store_id, limit]


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
    filter_clauses = []
    if filters:
        if filters.get("color"):
            filter_clauses.append(f" AND (variant_data IS NOT NULL AND (variant_data->>'color')::text ILIKE ${pos}) ")
            params.append(f"%{filters['color']}%")
            pos += 1
        if filters.get("size"):
            filter_clauses.append(f" AND (variant_data IS NOT NULL AND (variant_data->>'size')::text ILIKE ${pos}) ")
            params.append(f"%{filters['size']}%")
            pos += 1
    filter_sql = "".join(filter_clauses)
    params.append(limit)
    limit_pos = len(params)

    if sort_column in ("price", "created_at") and sort_order and sort_order.upper() in ("ASC", "DESC"):
        order_col = sort_column
        order_dir = sort_order.upper()
        order_sql = f"ORDER BY {order_col} {order_dir} NULLS LAST"
    else:
        order_sql = "ORDER BY ts_rank_cd(to_tsvector('english', title || ' ' || coalesce(content, '')), websearch_to_tsquery('english', $2)) DESC"

    sql = f"""
    SELECT id, title, content, price, url, image_url
    FROM store_knowledge
    WHERE store_id = $1
      AND data_type IN ('product', 'page', 'collect')
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
    filter_clauses = []
    if filters:
        if filters.get("color"):
            filter_clauses.append(f" AND (variant_data IS NOT NULL AND (variant_data->>'color')::text ILIKE ${pos}) ")
            params.append(f"%{filters['color']}%")
            pos += 1
        if filters.get("size"):
            filter_clauses.append(f" AND (variant_data IS NOT NULL AND (variant_data->>'size')::text ILIKE ${pos}) ")
            params.append(f"%{filters['size']}%")
            pos += 1
    filter_sql = "".join(filter_clauses)

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

    sql = f"""
    SELECT id, title, content, price, url, image_url
    FROM store_knowledge
    WHERE store_id = $1
      AND data_type IN ('product', 'page', 'collect')
      {keyword_sql}
      {filter_sql}
    ORDER BY {order_col} {order_dir} NULLS LAST
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
    """Path B: RRF hybrid. Empty keyword guard: if search_keywords empty, keyword CTE returns no rows."""
    params: list[Any] = [store_id, vector_json]
    pos = 3
    filter_clauses = []
    if filters:
        if filters.get("color"):
            filter_clauses.append(f" AND (variant_data IS NOT NULL AND (variant_data->>'color')::text ILIKE ${pos}) ")
            params.append(f"%{filters['color']}%")
            pos += 1
        if filters.get("size"):
            filter_clauses.append(f" AND (variant_data IS NOT NULL AND (variant_data->>'size')::text ILIKE ${pos}) ")
            params.append(f"%{filters['size']}%")
            pos += 1
    filter_sql = "".join(filter_clauses)

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

    # Keyword CTE: if no keywords, use WHERE 1=0 so we don't call plainto_tsquery('')
    if keyword_param_pos is not None:
        keyword_cte = f"""
    keyword_search AS (
        SELECT id, title, content, price, url, image_url,
               ROW_NUMBER() OVER (ORDER BY ts_rank_cd(to_tsvector('english', title || ' ' || coalesce(content, '')), plainto_tsquery('english', ${keyword_param_pos})) DESC) as rank_k
        FROM store_knowledge
        WHERE store_id = $1
          AND to_tsvector('english', title || ' ' || coalesce(content, '')) @@ plainto_tsquery('english', ${keyword_param_pos})
          {filter_sql}
        LIMIT 40
    )"""
    else:
        # Empty keyword CTE so plainto_tsquery is never called with empty string; same column shape for FULL OUTER JOIN
        keyword_cte = """
    keyword_search AS (
        SELECT id, title, content, price, url, image_url, 0::integer as rank_k
        FROM store_knowledge
        WHERE 1 = 0
    )"""

    final_score_expr = f"( COALESCE(${vector_w_pos} * (1.0 / (60 + v.rank_v)), 0.0) + COALESCE(${keyword_w_pos} * (1.0 / (60 + k.rank_k)), 0.0) )"

    sql = f"""
    WITH
    vector_search AS (
        SELECT id, title, content, price, url, image_url,
               ROW_NUMBER() OVER (ORDER BY embedding <=> $2::vector) as rank_v
        FROM store_knowledge
        WHERE store_id = $1
          AND data_type IN ('product', 'page', 'collect')
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
    ORDER BY {final_score_expr} DESC, COALESCE(v.id, k.id)
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
        filters = payload.get("filters") or {}
        if isinstance(filters, dict):
            filters = {"color": filters.get("color"), "size": filters.get("size")}
        else:
            filters = {}
        search_keywords = (payload.get("search_keywords") or "").strip()
        semantic_context = (payload.get("semantic_context") or "").strip()
        sort_column = payload.get("sort_column")
        sort_order = payload.get("sort_order")
        limit = int(payload.get("limit") or 5)
        limit = max(1, min(limit, 50))

        conn = connections.get("default")

        # Branch A: Catalog browse — no context, no keywords (e.g. "What is the cheapest product?")
        if not semantic_context :
            try:
                sql, params = _build_catalog_browse_sql(store_id, sort_column, sort_order, limit)
                rows = await conn.execute_query_dict(sql, params)
                return [dict(r) for r in rows] if rows else []
            except Exception as e:
                print(f"DatabaseExecutor Branch A (catalog) error: {e}", flush=True)
                return []

        # Branch B: Keyword only — no context, has keywords. No embedding API.
        if not semantic_context and search_keywords:
            try:
                sql, params = _build_keyword_only_sql(
                    store_id, search_keywords, filters, sort_column, sort_order, limit
                )
                rows = await conn.execute_query_dict(sql, params)
                return [dict(r) for r in rows] if rows else []
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
        try:
            sql, params = _build_path_b_sql(
                store_id,
                vector_json,
                search_keywords,
                vector_weight,
                keyword_weight,
                filters,
                sort_column,
                sort_order,
                limit,
            )
            rows = await conn.execute_query_dict(sql, params)
            return [dict(r) for r in rows] if rows else []
        except Exception as e:
            print(f"DatabaseExecutor Branch C (hybrid) error: {e}", flush=True)
            return []


async def execute_search(store_id: int, payload: dict) -> list[dict[str, Any]]:
    """Convenience async function: run DatabaseExecutor.execute_search."""
    return await DatabaseExecutor.execute_search(store_id, payload)
