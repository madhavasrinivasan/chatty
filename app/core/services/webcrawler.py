import asyncio
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig , VirtualScrollConfig , MemoryAdaptiveDispatcher
from langchain_core.documents import Document as LangchainDocument
from langchain_text_splitters import RecursiveCharacterTextSplitter
from llama_index.core import Document
from llama_index.core.node_parser import SimpleNodeParser
from langchain_community.document_loaders import PyPDFLoader
from app.core.schema.schema import llmresponse
from fastapi import Request
from datetime import datetime
from google import genai
from google.genai import types
from app.core.config.db import initialize_light_rag
from typing import List
import json
from app.core.config.config import settings
from app.core.schema.applicationerror import ApplicationError
from app.core.models.dbontrollers.admindbcontroller import AdminDbContoller
from app.core.schema.schema import StoreDNA
client = genai.Client()


class Services():

    @staticmethod
    async def crawlweb(urls: list[str]):
        browser_cfg = BrowserConfig(
            headless=True,
            viewport_width=1366,  # Smaller = less RAM
            viewport_height=768,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        )

        # Virtual scroll config for complete content capture
        virtual_scroll = VirtualScrollConfig(
            container_selector="body",        # Scroll entire page
            scroll_count=20,                  # Max scrolls (adjust based on site)
            scroll_by="page_height",          # Full page scrolls
            wait_after_scroll=1.5             # Wait for AJAX/content load
        )

        run_cfg = CrawlerRunConfig(
            wait_until="domcontentloaded",
            wait_for_timeout=15000,
            delay_before_return_html=2.0,
            wait_for_images=False,
            scan_full_page=True,              # Enable full page scanning
            scroll_delay=0.8,
            max_scroll_steps=20,              # Fallback scroll steps
            virtual_scroll_config=virtual_scroll,  # Proper nesting
            only_text=False,                  # Ensure markdown output (not plain text)
            js_code="""
                // Force lazy loading + full scroll
                window.scrollTo(0, document.body.scrollHeight);
                document.querySelectorAll('[loading="lazy"], [data-src]').forEach(el => {
                    el.src = el.dataset.src || el.src;
                    el.loading = 'eager';
                });
                // Trigger common infinite scroll patterns
                const loadMoreBtns = document.querySelectorAll('.load-more, [data-load-more]');
                loadMoreBtns.forEach(btn => btn?.click());
            """,
            css_selector="body *:not(script):not(style):not(noscript)",
            word_count_threshold=3,
            verbose=True,                     # Debug logging
        )

        async with AsyncWebCrawler(config=browser_cfg) as crawler:
            results = await crawler.arun_many(
                urls=urls,
                config=run_cfg,
                dispatcher=MemoryAdaptiveDispatcher(
                memory_threshold_percent=70.0,
                check_interval=1.0,
                max_session_permit=2  # 2 browsers max
            )
            )
        
        return results 

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
            response = client.models.embed_content(
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
    async def extract_pdf_pages_readable(pdf_path: str):
        loader = PyPDFLoader(pdf_path)
        docs = loader.load()
         
        pages = []

        for doc in docs:
            text = doc.page_content
            meta = doc.metadata

            if not text or not text.strip():
                continue

            pages.append({
                "text": text,
                "page_number": meta.get("page_label") or meta.get("page", 0) + 1,
                "file_name": meta.get("source").split("/")[-1],
                "total_pages": meta.get("total_pages"),
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
        response = client.models.embed_content(
            model="gemini-embedding-001",
            contents=[text],
            config=types.EmbedContentConfig(task_type="RETRIEVAL_QUERY", output_dimensionality=768)
        )
        # response.embeddings is a list of embedding objects; take the first one's values
        if not response.embeddings:
            return []
        emb = response.embeddings[0]
        return list(emb.values) if hasattr(emb, "values") else []

    
    @staticmethod
    async def generate_batch_embeddings(texts: list[str]):
        # 1. Handle empty lists to avoid API errors
        if not texts:
            return []

        # 2. Send the LIST directly to 'contents'
        response = client.models.embed_content(
            model="gemini-embedding-001", # Recommendation: Use 004 (Newer/Better)
            contents=texts,
            config=types.EmbedContentConfig(
                task_type="RETRIEVAL_DOCUMENT", # <--- CORRECT TYPE FOR DB STORAGE
                output_dimensionality=768
            )
        )
        
        
        return [e.values for e in response.embeddings] 
    
    @staticmethod
    async def vectiriseproductblob(content_blob_array: list):
        try:
           embeddings =  await Services.generate_batch_embeddings(content_blob_array); 
           return embeddings
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
            response = client.models.generate_content(
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
            
            response = client.models.generate_content(
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