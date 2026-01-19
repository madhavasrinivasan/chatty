#!/usr/bin/env python
"""
RQ Worker Script
Run this separately to process background tasks from Redis queue.

Usage:
    uv run python worker.py
    or
    rq worker background_tasks --url redis://localhost:6379
"""
import asyncio
from rq import Worker, Queue, Connection
from redis import Redis
from app.core.config.db import init_redis

# Initialize Redis connection
redis_conn, redis_queue = init_redis()

if redis_conn is None:
    print("Error: Could not connect to Redis. Make sure Redis is running.")
    exit(1)

def run_async_task(coro):
    """Helper to run async functions in RQ worker"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()

if __name__ == '__main__':
    with Connection(redis_conn):
        worker = Worker(['background_tasks'])
        print(f"Starting RQ worker for queue: background_tasks")
        print(f"Redis connection: {redis_conn}")
        worker.work()
