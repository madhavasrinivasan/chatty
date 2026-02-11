from app.core.services.webcrawler import Services 
from fastapi import FastAPI,APIRouter, UploadFile, File, Form, Depends, Request
from app.core.config.db import init_db, close_db ,initialize_light_rag
from fastapi.middleware.cors import CORSMiddleware
import uvicorn as uvicorn
from app.admin.routes.index import approuter
from app.core.schema.errorschema import register_exception_handlers 
from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app: FastAPI):
    # DB init (your existing logic)
    await init_db()

    # RAG instances per store (lazy-loaded). Key: store_id, value: LightRAG instance.
    app.state.rags = {}
    print("RAG Storage Initialized and Ready!")

    yield  # <-- app runs here

    # Shutdown logic
    await close_db()
    print("Stopping server...")







app = FastAPI(lifespan=lifespan)

# Register exception handlers
register_exception_handlers(app) 

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(approuter) 



@app.get("/health")
async def health():
    return {"status": "healthy"}  



@app.on_event("startup")
async def startup():
    await init_db()

    
@app.on_event("shutdown")
async def shutdown():
    await close_db()
    # close_redis()


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=3009, reload=True)