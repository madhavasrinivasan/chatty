from app.admin.routes.authroutes import adminauthrouter
from app.admin.routes.approutes import adminapprouter
from app.admin.routes.sessionroutes import sessionrouter
from fastapi import APIRouter


approuter = APIRouter(
    prefix="/admin",
    tags=["admin"],
) 

approuter.include_router(adminauthrouter);
approuter.include_router(adminapprouter);
approuter.include_router(sessionrouter);

