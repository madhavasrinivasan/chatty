from tortoise import Tortoise
from app.core.config.config import settings
from redis import Redis
from rq import Queue


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



redis_conn = None
redis_queue = None

def init_redis():
    global redis_conn, redis_queue
    redis_conn = Redis(host="localhost", port=6379, decode_responses=True)
    redis_queue = Queue("background_tasks", connection=redis_conn)
    return redis_conn, redis_queue

def close_redis():
    global redis_conn
    if redis_conn:
        redis_conn.close()


