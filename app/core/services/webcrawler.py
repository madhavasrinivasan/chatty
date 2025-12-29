import asyncio
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig

async def crawlweb():
    browser_cfg = BrowserConfig(headless=True)

    async with AsyncWebCrawler(config=browser_cfg) as crawler:
        run_cfg = CrawlerRunConfig(
            wait_for="body",
            wait_for_timeout=10000,
            delay_before_return_html=5.0,
            wait_for_images=True,
            scan_full_page=True,
            scroll_delay=0.3,
            max_scroll_steps=None,
        )

        result = await crawler.arun(
            url="https://in.bookmyshow.com/movies/erode/chainsaw-man-the-movie-reze-arc/ET00448819",
            config=run_cfg
        )

        print(result.markdown)

