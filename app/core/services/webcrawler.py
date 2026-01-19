import asyncio
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from llama_index.core.node_parser import LangchainNodeParser
from langchain_community.document_loaders import PyPDFLoader
from datetime import datetime
from google import genai
from google.genai import types
from app.core.config.config import settings

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
            splitter = RecursiveCharacterTextSplitter(
                chunk_size=800,
                chunk_overlap=120
            )

            node_parser = LangchainNodeParser(splitter)

            nodes = node_parser.get_nodes_from_documents(documents)

            return nodes

   
    @staticmethod
    async def embed_nodes_in_batches(nodes,batch_size=16):
        def batch(items, size):
            for i in range(0, len(items), size):
                yield items[i:i + size]

        texts = [node.text for node in nodes]
        embeddings = []
        for text_batch in batch(texts, batch_size):
            response = client.models.embed_content.embed(
                model="gemini-embedding-001",
                contents=text_batch,
                config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT")
            )
            embeddings.extend(response)

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
     

    @staticmethod
    async def crawl_results_to_documents(results, base_metadata):
        documents = []

        for result in results:
            if not result.markdown or not result.text:
                continue


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