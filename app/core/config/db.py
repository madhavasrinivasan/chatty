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
    """Initialize Redis connection and queue. Returns True if successful, False otherwise."""
    global redis_conn, redis_queue
    try:
        redis_conn = Redis(host="localhost", port=6379, decode_responses=True)
        # Test the connection
        redis_conn.ping()
        redis_queue = Queue("background_tasks", connection=redis_conn)
        print("Redis initialized successfully")
        return redis_conn, redis_queue
    except Exception as e:
        print(f"Warning: Failed to initialize Redis: {e}")
        print("Background tasks will not be available. Make sure Redis is running.")
        redis_conn = None
        redis_queue = None
        return None, None

def close_redis():
    global redis_conn
    if redis_conn:
        redis_conn.close()


