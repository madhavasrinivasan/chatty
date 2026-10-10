import asyncio
from langchain_core.documents import Document as LangchainDocument
from langchain_text_splitters import RecursiveCharacterTextSplitter
from llama_index.core import Document
from llama_index.core.node_parser import SimpleNodeParser
from langchain_community.document_loaders import PyPDFLoader
from app.core.schema.schema import llmresponse
from fastapi import Request
from datetime import datetime
import httpx
import base64
import io
from google import genai
from google.genai import types
from app.core.config.db import initialize_light_rag
from typing import List
import json
from app.core.config.config import settings
from app.core.config.gemini_client import get_genai_client, get_genai_embedding_client
from app.core.schema.applicationerror import ApplicationError
from app.core.models.dbontrollers.admindbcontroller import AdminDbContoller
from app.core.schema.schema import StoreDNA


def _get_client() -> genai.Client:
    return get_genai_client()


def _get_embedding_client() -> genai.Client:
    return get_genai_embedding_client()


class Services():

    @staticmethod
    async def crawlweb(urls: list[str]):
        # crawl4ai removed (no Alpine musl wheels for patchright). Use Shopify/Comez ingest instead.
        raise ApplicationError.SomethingWentWrong(
            "Web crawling is disabled. Sync catalog via Shopify/Comez ingest."
        ) 

    @staticmethod
    async def documents_to_nodes(documents):
        try:
            # Convert dictionaries to Langchain Document objects if needed
            langchain_documents = []
            for doc in documents:
                if isinstance(doc, dict):
                    # Convert dict to LangchainDocument
                    langchain_doc = LangchainDocument(
                        page_content=doc.get("page_content", ""),
                        metadata=doc.get("metadata", {})
                    )
                    langchain_documents.append(langchain_doc)
                elif isinstance(doc, LangchainDocument):
                    # Already a LangchainDocument
                    langchain_documents.append(doc)
                else:
                    # Try to use as-is (might be another Document type)
                    langchain_documents.append(doc)
            
            # Use RecursiveCharacterTextSplitter with chunk_size and chunk_overlap
            node_parser = RecursiveCharacterTextSplitter(
                chunk_size=16000,
                chunk_overlap=1800
            )

            nodes = node_parser.split_documents(langchain_documents)

            return nodes
        except Exception as e:
            print(f"Error converting documents to nodes: {e}")
            raise ApplicationError.InternalServerError("Cannot convert documents to nodes")

   
    @staticmethod
    async def embed_nodes_in_batches(nodes,batch_size=16):
    
        def batch(items, size):
            for i in range(0, len(items), size):
                yield items[i:i + size]

        # Langchain Document objects use .page_content, not .text
        texts = [node.page_content for node in nodes]
        embeddings = []
        for text_batch in batch(texts, batch_size):
            response = _get_embedding_client().models.embed_content(
                model="gemini-embedding-001",
                contents=text_batch,
                config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT", output_dimensionality=768)
            )
            
            # Extract embeddings from the response
            # response.embeddings is a list of embedding objects
            # Each embedding object has a .values attribute containing the embedding vector (list of floats)
            for emb_obj in response.embeddings:
                print(f"Emb obj: {emb_obj}")
                # Extract the values attribute which contains the actual embedding vector
                embeddings.append(list(emb_obj.values))

        # Store embeddings in metadata since Document objects don't allow arbitrary attributes
        for node, emb in zip(nodes, embeddings):
            node.metadata["embedding"] = emb

        return nodes



    @staticmethod
    async def extract_pdf_pages_readable(pdf_path: str, max_pages: int | None = 50):
        """
        Extract non-empty PDF pages as readable text.
        If max_pages is set, only the first max_pages pages of the PDF are considered
        (empty pages still count toward the cap so page numbers stay consistent).
        Raises ValueError if the PDF has more pages than max_pages.
        """
        loader = PyPDFLoader(pdf_path)
        docs = loader.load()

        total_pdf_pages = len(docs)
        if max_pages is not None and total_pdf_pages > max_pages:
            raise ValueError(
                f"PDF has {total_pdf_pages} pages; maximum allowed is {max_pages}."
            )

        pages = []
        for doc in docs[: max_pages if max_pages is not None else None]:
            text = doc.page_content
            meta = doc.metadata

            if not text or not text.strip():
                continue

            source = meta.get("source") or pdf_path
            pages.append({
                "text": text,
                "page_number": meta.get("page_label") or meta.get("page", 0) + 1,
                "file_name": str(source).split("/")[-1],
                "total_pages": meta.get("total_pages") or total_pdf_pages,
            })

        return pages

    @staticmethod
    async def crawl_results_to_documents(results, base_metadata):
        documents = []

        for result in results:
            if not result.markdown or result.markdown == "":
                continue

            # print(f"Result: {result.markdown}")
            documents.append(
                {
                    "page_content": result.markdown,
                    "metadata": {
                        "source_type": "web",
                        "url": result.url,                 
                        "chatbot_id": base_metadata["chatbot_id"],
                        "user_id": base_metadata["user_id"],
                        "ingested_at": datetime.utcnow().isoformat()

                    }
                }
            ) 



        return documents 



    @staticmethod
    async def generate_embedding(text: str):
        # google-genai embed_content is sync — must not run on the event loop or
        # one slow/hung embed freezes *all* FastAPI requests (looks like timeouts).
        embedding_model = getattr(settings, "gemini_embedding_model", "gemini-embedding-2") or "gemini-embedding-2"
        dim = int(getattr(settings, "gemini_embedding_dimension", 768) or 768)

        def _embed(model_name: str):
            response = _get_embedding_client().models.embed_content(
                model=model_name,
                contents=[text],
                config=types.EmbedContentConfig(
                    task_type="RETRIEVAL_QUERY", output_dimensionality=dim
                ),
            )
            if not response.embeddings:
                return []
            emb = response.embeddings[0]
            return list(emb.values) if hasattr(emb, "values") else []

        try:
            try:
                return await asyncio.wait_for(asyncio.to_thread(_embed, embedding_model), timeout=15.0)
            except Exception as primary_err:
                if embedding_model != "gemini-embedding-001":
                    print(f"⚠️ Primary embed model '{embedding_model}' error ({primary_err}), falling back to gemini-embedding-001", flush=True)
                    return await asyncio.wait_for(asyncio.to_thread(_embed, "gemini-embedding-001"), timeout=15.0)
                raise primary_err
        except asyncio.TimeoutError:
            print("generate_embedding timed out after 15s", flush=True)
            return []
        except Exception as e:
            print(f"generate_embedding error: {e}", flush=True)
            return []

    @staticmethod
    async def describe_image_visually(
        image_bytes: bytes,
        mime_type: str = "image/jpeg",
    ) -> str:
        """
        Use Gemini Flash Lite to generate rich e-commerce visual search keywords from an image.
        This provides instant, zero-shot visual tagging to seed both the embedding and BM25 keywords.
        """
        try:
            client = _get_client()
            part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
            prompt = (
                "You are an e-commerce visual search assistant. "
                "Analyze this product image and output a concise 6 to 12 word description "
                "of what is shown (product/garment category, color, material, pattern, and key distinguishing features). "
                "Output ONLY the descriptive keywords separated by spaces, with no commentary or punctuation."
            )
            response = await asyncio.to_thread(
                client.models.generate_content,
                model=settings.gemini_router_model or "gemini-2.5-flash-lite",
                contents=[part, prompt],
            )
            desc = (getattr(response, "text", "") or "").strip()
            print(f"👁️ Visual tagging for photo: {desc!r}", flush=True)
            return desc
        except Exception as e:
            print(f"⚠️ Visual tagging error: {e}", flush=True)
            return ""

    @staticmethod
    async def resolve_image_bytes(
        image_bytes: bytes | None = None,
        image_url: str | None = None,
    ) -> tuple[bytes | None, str]:
        """
        Resolves image bytes and mime type from either raw bytes or an HTTP URL.
        Handles Shopify CDN thumbnail optimization (?width=300), redirects, and robust error handling.
        """
        if image_bytes:
            return image_bytes, "image/jpeg"

        if not image_url:
            return None, "image/jpeg"

        try:
            fetch_url = image_url.strip()
            if "cdn.shopify.com" in fetch_url and "?" not in fetch_url:
                fetch_url = f"{fetch_url}?width=300"

            async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
                resp = await client.get(fetch_url)
                if resp.status_code == 200:
                    content_type = resp.headers.get("content-type", "").lower()
                    if "png" in content_type:
                        mime_type = "image/png"
                    elif "webp" in content_type:
                        mime_type = "image/webp"
                    else:
                        mime_type = "image/jpeg"
                    return resp.content, mime_type
                else:
                    print(f"⚠️ Failed downloading image ({resp.status_code}) from: {image_url}", flush=True)
        except Exception as dl_err:
            print(f"⚠️ Error resolving image bytes from {image_url}: {dl_err}", flush=True)

        return None, "image/jpeg"

    @staticmethod
    async def generate_multimodal_embedding(
        text: str = "",
        image_bytes: bytes | None = None,
        image_url: str | None = None,
        mime_type: str = "image/jpeg",
        task_type: str = "RETRIEVAL_QUERY",
    ):
        """
        Generates a unified multimodal embedding from text and/or an image using Gemini Embedding 2.
        Supports:
          - Image only: visual similarity search ("Find me something like this").
          - Text only: semantic search ("red floral summer dress").
          - Multimodal (Image + Text): compound query (e.g. photo + "in blue color under $50").
        """
        embedding_model = getattr(settings, "gemini_embedding_model", "gemini-embedding-2") or "gemini-embedding-2"
        dim = int(getattr(settings, "gemini_embedding_dimension", 768) or 768)

        # 1. Download/resolve image bytes if given an image URL
        if not image_bytes and image_url:
            image_bytes, mime_type = await Services.resolve_image_bytes(None, image_url)

        # 2. Build contents list
        contents = []
        if text and text.strip():
            contents.append(text.strip())
        if image_bytes:
            contents.append(types.Part.from_bytes(data=image_bytes, mime_type=mime_type))

        if not contents:
            return []

        # If no image was acquired, fall back to text embedding
        if not image_bytes:
            return await Services.generate_embedding(text)

        # 3. Try native multimodal embedding with gemini-embedding-2
        def _embed_multimodal(model_name: str):
            response = _get_embedding_client().models.embed_content(
                model=model_name,
                contents=contents,
                config=types.EmbedContentConfig(
                    task_type=task_type,
                    output_dimensionality=dim,
                ),
            )
            if not response.embeddings:
                return []
            emb = response.embeddings[0]
            return list(emb.values) if hasattr(emb, "values") else []

        try:
            emb = await asyncio.wait_for(asyncio.to_thread(_embed_multimodal, embedding_model), timeout=15.0)
            if emb:
                return emb
        except Exception as primary_err:
            print(f"⚠️ Native multimodal embed error on '{embedding_model}': {primary_err}", flush=True)

        # 4. If SDK strips non-text parts or native call fails, use Gemini Flash Lite zero-shot visual tagging
        if image_bytes:
            visual_desc = await Services.describe_image_visually(image_bytes, mime_type)
            combined_query = f"{visual_desc} {text}".strip() if text else visual_desc
            if combined_query:
                print(f"🔍 Generating embedding from visual description: {combined_query!r}", flush=True)
                return await Services.generate_embedding(combined_query)

        if text:
            return await Services.generate_embedding(text)
        return []

    @staticmethod
    async def generate_batch_embeddings(texts: list[str]):
        # 1. Handle empty lists to avoid API errors
        if not texts:
            return []

        embedding_model = getattr(settings, "gemini_embedding_model", "gemini-embedding-2") or "gemini-embedding-2"
        dim = int(getattr(settings, "gemini_embedding_dimension", 768) or 768)

        def _embed_batch(model_name: str):
            response = _get_embedding_client().models.embed_content(
                model=model_name,
                contents=texts,
                config=types.EmbedContentConfig(
                    task_type="RETRIEVAL_DOCUMENT",
                    output_dimensionality=dim,
                ),
            )
            return [list(e.values) for e in (response.embeddings or [])]

        try:
            try:
                return await asyncio.wait_for(asyncio.to_thread(_embed_batch, embedding_model), timeout=60.0)
            except Exception as primary_err:
                if embedding_model != "gemini-embedding-001":
                    print(f"⚠️ Batch embed '{embedding_model}' error ({primary_err}), falling back to gemini-embedding-001", flush=True)
                    return await asyncio.wait_for(asyncio.to_thread(_embed_batch, "gemini-embedding-001"), timeout=60.0)
                raise primary_err
        except asyncio.TimeoutError:
            print("generate_batch_embeddings timed out", flush=True)
            return []
        except Exception as e:
            print(f"generate_batch_embeddings error: {e}", flush=True)
            return []

    @staticmethod
    async def vectiriseproductblob(products_or_blobs: list):
        """
        Generates embeddings for a batch of products.
        Accepts either:
          - A list of product dicts (each having 'content' and optional 'image_url')
          - A list of plain text strings
        """
        try:
            if not products_or_blobs:
                return []
            if isinstance(products_or_blobs[0], str):
                return await Services.generate_batch_embeddings(products_or_blobs)
            texts = [p.get("content", "") for p in products_or_blobs]
            return await Services.generate_batch_embeddings(texts)
        except Exception as e:
            print(f"Error vectorizing product blob: {e}")
            raise ApplicationError.InternalServerError("Cannot vectorize product blob")

    @staticmethod
    async def generate_store_dna_from_titles(store_id: int):
        """
        Build a lightweight 'store DNA' summary from product titles and the About page,
        then store it on the ecom_store.store_dna column.
        """
        controller = AdminDbContoller()
        try:
            # 1. Sample up to 30 product titles for this store
            products_qs = controller.models.store_knowledge.filter(
                store_id=store_id,
                data_type="product",
            )
            titles = await products_qs.limit(30).values_list("title", flat=True)

            # 2. Get all 'About' page content (if any) and concatenate
            about_qs = controller.models.store_knowledge.filter(
                store_id=store_id,
                data_type="page",
                title__icontains="about",
            )
            about_pages = await about_qs.all()
            about_chunks = []
            for page in about_pages:
                try:
                    text = (page.content or "").strip()
                except AttributeError:
                    text = ""
                if text:
                    about_chunks.append(text)
            about_text = "\n\n".join(about_chunks)

            # 3. Prompt the model for DNA JSON
            dna_prompt = f"""
Please analyze this Shopify store.

Product Titles: {', '.join(titles)}
About Us: {about_text}
"""
            schema = StoreDNA.model_json_schema()
            response = _get_client().models.generate_content(
                model="gemini-2.5-flash",
                contents=dna_prompt,
                config={
                    "response_mime_type": "application/json",
                    "response_json_schema": schema,
                },
            )
            raw_text = getattr(response, "text", None) or str(response)
            dna_data = json.loads(raw_text)

            dna_summary = dna_data.get("dna_summary", "")

            # 4. Persist dna_summary on ecom_store for this store_id
            if dna_summary:
                await controller.update_store_dna(store_id=store_id, dna_summary=dna_summary)

            return dna_data
        except Exception as e:
            print(f"Error generating store DNA: {e}")
            raise ApplicationError.InternalServerError("Cannot generate store DNA")


    @staticmethod
    async def generate_response(vector_store: List[dict]):
        try: 
            #Format vector_store content for the prompt
            context_parts = []
            for item in vector_store:
                content = item.get("content", "")
                metadata = item.get("metadata", {})
                if isinstance(metadata, str):
                    try:
                        metadata = json.loads(metadata)
                    except:
                        metadata = {}
                
                source_info = []
                if metadata.get("url"):
                    source_info.append(f"URL: {metadata['url']}")
                if metadata.get("file_name"):
                    source_info.append(f"File: {metadata['file_name']}")
                if metadata.get("page_number"):
                    source_info.append(f"Page: {metadata['page_number']}")
                
                source_str = " | ".join(source_info) if source_info else "Unknown source"
                context_parts.append(f"[Source: {source_str}]\n{content}\n")
            
            context_text = "\n---\n".join(context_parts)
            
            prompt = f"""
You are a helpful AI assistant answering questions using ONLY the provided context.

### Rules (VERY IMPORTANT)
- Use ONLY the information in the context below.
- If the answer is not present in the context, say **"I don't know based on the provided sources."**
- Do NOT make up facts.
- Write the answer in **clear markdown**.
- Cite sources in a separate list.
- donot give directly the answer, give the answer in a way that is easy to understand and follow.

----

### Context
{context_text}
"""
            print(f"Vector store: {context_text}")
            
            # Convert Pydantic model to JSON schema
            json_schema = llmresponse.model_json_schema()
            
            response = _get_client().models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "response_json_schema": json_schema
                },
            )
            return response
        except Exception as e:
            print(f"Error generating response: {e}")
            raise ApplicationError.InternalServerError("Cannot generate response") 




    @staticmethod
    async def get_light_rag_for_store(request: Request, store_id: str):
        """
        Retrieve a LightRAG instance for a specific store from FastAPI app state.
        If not present, initialize it and store in app state.
        """
        rags: dict = request.app.state.rags
        if store_id in rags:
            return rags[store_id]
       

        rag = await initialize_light_rag(store_id=store_id)  # adjust signature to accept store_id
        rags[store_id] = rag
        return rag 
    
    @staticmethod
    async def insert_products_to_database(products_list: list, chatbot_id: int):
        try:
            # print(f"Products list: {products_list}")
            content_blob_array = []
            for product in products_list:
                content_blob_array.append(product.get("content", ""))
            embeddings = await Services.vectiriseproductblob(content_blob_array)
            for product, embedding in zip(products_list, embeddings):
                product["embedding"] = embedding
            await AdminDbContoller().insert_products_to_database(products_list, chatbot_id)
               

        except Exception as e:
            print(f"Error inserting products to database: {e}")
            raise ApplicationError.InternalServerError("Cannot insert products to database")