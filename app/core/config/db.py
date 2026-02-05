from tortoise import Tortoise
from app.core.config.config import settings


async def init_db():
    # Tortoise ORM expects 'postgres://' not 'postgresql://'
    db_url = settings.database_url.replace("postgresql://", "postgres://", 1)
    
    await Tortoise.init(
        db_url=db_url,
        modules={"models": ["app.core.models"]},
    )

    if settings.env == "development":
        await Tortoise.generate_schemas(safe=True)


async def close_db():
    await Tortoise.close_connections()
