import asyncio
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig
from langchain_core.documents import Document as LangchainDocument
from langchain_text_splitters import RecursiveCharacterTextSplitter
from llama_index.core import Document
from llama_index.core.node_parser import SimpleNodeParser
from langchain_community.document_loaders import PyPDFLoader
from app.core.schema.schema import llmresponse
from datetime import datetime
from google import genai
from google.genai import types
from typing import List
import json
from app.core.config.config import settings
from app.core.schema.applicationerror import ApplicationError
client = genai.Client()


class Services():

    @staticmethod
    async def crawlweb(urls: list[str]):
        browser_cfg = BrowserConfig(
            headless=True,
            viewport_width=1366,
            viewport_height=768,
        )

        run_cfg = CrawlerRunConfig(
            wait_for="body",                
            wait_for_timeout=8000,           
            delay_before_return_html=1.0,   
            wait_for_images=False,          
            scan_full_page=False,            
            scroll_delay=0.3,
            max_scroll_steps=3,              
        )

        async with AsyncWebCrawler(config=browser_cfg) as crawler:
            results = await crawler.arun_many(
                urls=urls,
                config=run_cfg
            )

        return results 

    @staticmethod
    async def documents_to_nodes(documents):
        try:
            print(f"Documents: {documents}")
            llama_docs = []
            for doc in documents:
                if isinstance(doc, dict):
                    # Convert dict to llama_index Document
                    llama_docs.append(Document(
                        text=doc.get("page_content", doc.get("text", "")),
                        metadata=doc.get("metadata", {})
                    ))
                elif isinstance(doc, LangchainDocument):
                    # Convert langchain Document to llama_index Document
                    llama_docs.append(Document(
                        text=doc.page_content,
                        metadata=doc.metadata
                    ))
                else:
                    # Assume it's already a llama_index Document
                    llama_docs.append(doc)
            
            # Use SimpleNodeParser with chunk_size and chunk_overlap
            node_parser = SimpleNodeParser.from_defaults(
                chunk_size=800,
                chunk_overlap=120
            )

            nodes = node_parser.get_nodes_from_documents(llama_docs)

            return nodes
        except Exception as e:
            print(f"Error converting documents to nodes: {e}")
            raise ApplicationError.InternalServerError("Cannot convert documents to nodes")

   
    @staticmethod
    async def embed_nodes_in_batches(nodes,batch_size=16):
    
        def batch(items, size):
            for i in range(0, len(items), size):
                yield items[i:i + size]

        texts = [node.text for node in nodes]
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
                # Extract the values attribute which contains the actual embedding vector
                embeddings.append(list(emb_obj.values))

        for node, emb in zip(nodes, embeddings):
            node.embedding = emb

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

            print(f"Pages: {pages}")
        
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
        return response.embeddings[0].values 



    @staticmethod
    async def generate_response(vector_store: List[dict]):
        try: 
            # Format vector_store content for the prompt
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

----

### Context
{context_text}
"""
            print(f"Context text: {context_text}")
            
            # Convert Pydantic model to JSON schema
            json_schema = llmresponse.model_json_schema()
            
            response = client.models.generate_content(
                model="gemini-2.5-flash-lite",
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