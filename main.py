from app.core.services.webcrawler import Services 
from fastapi import FastAPI,APIRouter, UploadFile, File, Form, Depends
from app.core.config.db import init_db, close_db

app = FastAPI() 




@app.get("/health")
async def health():
    return {"status": "healthy"}  



@app.on_event("startup")
async def startup():
    await init_db()

@app.on_event("shutdown")
async def shutdown():
    await close_db()
