from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, HttpUrl
from typing import List
from bs4 import BeautifulSoup
import aiohttp
import asyncio
import logging
import re
from fastapi.responses import JSONResponse

# --- Setup Logging ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- FastAPI App ---
app = FastAPI(
    title="Async Web Scraping API",
    description="Scrapes content and metadata from given URLs",
    version="1.1"
)

# --- Pydantic Models ---
class URLRequest(BaseModel):
    url: HttpUrl

class URLListRequest(BaseModel):
    urls: List[HttpUrl]

# --- Clean the scrapped page content  ---

def clean_text(text):
    import re
    # Collapse multiple line breaks
    text = re.sub(r'\n{2,}', '\n', text)
    # Remove nav/footer noise (optional)
    blacklist_keywords = ["Sign In", "Sign Out", "All Articles", "Home"]
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    lines = [line for line in lines if all(bad not in line for bad in blacklist_keywords)]
    return lines  # return as list of paragraphs

# --- HTML Fetch and Scrape ---
async def fetch_html(session: aiohttp.ClientSession, url: str) -> str:
    try:
        async with session.get(url, timeout=10) as response:
            if response.status != 200:
                logger.error(f"[{url}] - HTTP {response.status}")
                raise HTTPException(status_code=response.status, detail=f"Failed to fetch {url}")
            html = await response.text()
            logger.info(f"[{url}] - Fetched successfully")
            return html
    except asyncio.TimeoutError:
        logger.error(f"[{url}] - Timeout")
        raise HTTPException(status_code=504, detail=f"Timeout while fetching {url}")
    except Exception as e:
        logger.exception(f"[{url}] - Unexpected fetch error")
        raise HTTPException(status_code=500, detail=f"Error fetching {url}: {str(e)}")

async def scrape_url(session: aiohttp.ClientSession, url: str) -> dict:
    try:
        html = await fetch_html(session, url)
        soup = BeautifulSoup(html, "html.parser")

        title = soup.title.string.strip() if soup.title and soup.title.string else ""

        meta_description = ""
        desc_tag = soup.find("meta", attrs={"name": "description"})
        if desc_tag and desc_tag.get("content"):
            meta_description = desc_tag["content"]

        meta_keywords = ""
        keywords_tag = soup.find("meta", attrs={"name": "keywords"})
        if keywords_tag and keywords_tag.get("content"):
            meta_keywords = keywords_tag["content"]

        headers = [h.get_text(strip=True) for h in soup.find_all(['h1', 'h2'])]

        main_content = soup.find("main") or soup.find("article") or soup.body
        content_text = main_content.get_text(separator="\n", strip=True) if main_content else ""

        return {
            "url": url,
            "metadata": {
                "title": title,
                "meta_description": meta_description,
                "meta_keywords": meta_keywords,
                "headers": headers
            },
            "content": content_text
        }

    except Exception as e:
        logger.exception(f"[{url}] - Error scraping page")
        raise HTTPException(status_code=500, detail=f"Error parsing HTML for {url}: {str(e)}")

# --- API Endpoints ---

@app.post("/scrape/single", summary="Scrape a single web page", tags=["Scraper"])
async def scrape_single(request: URLRequest):
    async with aiohttp.ClientSession() as session:
        result = await scrape_url(session, str(request.url))
        return result

@app.post("/scrape/batch", summary="Scrape multiple web pages", tags=["Scraper"])
async def scrape_batch(request: URLListRequest):
    async with aiohttp.ClientSession() as session:
        tasks = [scrape_url(session, str(url)) for url in request.urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        output = []
        for url, result in zip(request.urls, results):
            if isinstance(result, Exception):
                logger.warning(f"[{url}] - Failed: {str(result)}")
                output.append({
                    "url": str(url),
                    "error": str(result)
                })
            else:
                output.append(result)

        return output

# --- Global Exception Handler ---
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})
