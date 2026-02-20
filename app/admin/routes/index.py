from app.admin.routes.authroutes import adminauthrouter
from app.admin.routes.approutes import adminapprouter
from fastapi import APIRouter


approuter = APIRouter(
    prefix="/admin",
    tags=["admin"],
) 

approuter.include_router(adminauthrouter);
approuter.include_router(adminapprouter);


