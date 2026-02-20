from app.core.services.webcrawler import Services 
from fastapi import FastAPI,APIRouter, UploadFile, File, Form, Depends, Request
from app.core.config.db import init_db, close_db ,initialize_light_rag
from fastapi.middleware.cors import CORSMiddleware
import uvicorn as uvicorn
from fastapi.responses import HTMLResponse
from app.admin.routes.index import approuter
from app.core.schema.errorschema import register_exception_handlers 
from contextlib import asynccontextmanager
from app.admin.controller.appcontroller import AppController
import httpx
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


@app.get("/")
async def root(request: Request):
    # Shopify hits GET / with ?shop=...&id_token=...; must return JSON, not HTML
    # shop = request.query_params.get("shop")
    # id_token = request.query_params.get("id_token")

    # # CASE 1: We have the Golden Ticket (id_token)
    # if shop and id_token:
    #     print(f"⚡️ FOUND ID_TOKEN for {shop}! Exchanging it now...")
        
    #     try:
    #         async with httpx.AsyncClient() as client:
    #             exchange_url = f"https://{shop}/admin/oauth/access_token"
    #             payload = {
    #                 "client_id": API_KEY,
    #                 "client_secret": API_SECRET,
    #                 "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
    #                 "subject_token": id_token,
    #                 "subject_token_type": "urn:ietf:params:oauth:token-type:id_token",
    #                 "requested_token_type": "urn:shopify:params:oauth:token-type:offline-access-token"
    #             }
                
    #             response = await client.post(exchange_url, json=payload)
                
    #             if response.status_code == 200:
    #                 data = response.json()
    #                 access_token = data.get("access_token")
                    
    #                 # 🛑 SUCCESS! This prints your permanent token 🛑
    #                 print(f"\n🎉 VICTORY! Permanent Access Token: {access_token}\n")
                    
    #                 # TODO: Save this to your DB now!
    #                 # db.save(shop, access_token)
                    
    #                 return f"<h1>Connected!</h1><p>Token received and saved.</p>"
    #             else:
    #                 print(f"❌ Exchange Failed: {response.text}")
    #                 return f"<h1>Error exchanging token</h1><p>{response.text}</p>"
                    
    #     except Exception as e:
    #         return f"<h1>Server Error</h1><p>{str(e)}</p>"

    # print(f"🚫 No token found. Redirecting to Install...")
    # auth_url = (
    #     f"https://{shop}/admin/oauth/authorize?"
    #     f"client_id={API_KEY}&"
    #     f"scope=read_products,read_inventory,read_orders&"
    #     f"redirect_uri={REDIRECT_URI}"
    # )
    # return f"""<script>window.top.location.href = "{auth_url}";</script>"""
    return await AppController.shopify_callback(request)


@app.get("/api/auth/oauth/callback")
async def shopify_callback(request:Request):
    return await AppController.shopify_callback(request)
    
if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=3009, reload=True)