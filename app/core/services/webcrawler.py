import asyncio
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from llama_index.core.node_parser import LangchainNodeParser



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
    async def embed_nodes_in_batches(nodes, embed_client, batch_size=16):
        def batch(items, size):
            for i in range(0, len(items), size):
                yield items[i:i + size]

        texts = [node.text for node in nodes]
        embeddings = []
        for text_batch in batch(texts, batch_size):
            response = embed_client.embed(
                texts=text_batch,
                task_type="RETRIEVAL_DOCUMENT"
            )
            embeddings.extend(response)

        for node, emb in zip(nodes, embeddings):
            node.embedding = emb

        return nodes

 
