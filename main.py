from core.services.webcrawler import crawlweb 
from fastapi import FastAPI,APIRouter, UploadFile, File, Form, Depends

app = FastAPI() 




@app.get("/health")
async def health():
    return {"status": "healthy"} 



