import asyncio
import os

# Set environment variable before any imports (macOS fork() safety)
os.environ["OBJC_DISABLE_INITIALIZE_FORK_SAFETY"] = "YES"

from app.core.config.db import init_db
from app.core.models.dbontrollers.admindbcontroller import AdminDbContoller
from app.admin.controller.appcontroller import AppController
from app.core import models as Models
from app.core.services.webcrawler import Services


async def process_background_tasks():
    """Poll database every 3 seconds for pending background tasks and process them. Keeps running forever."""
    controller = AdminDbContoller()
    poll_interval = 3

    while True:
        print("Polling for tasks...", flush=True)
        try:
            pending_tasks = await asyncio.wait_for(
                controller.get_pending_background_tasks(),
                timeout=10.0,
            )
        except asyncio.TimeoutError:
            print("Warning: get_pending_background_tasks timed out (10s), retrying...", flush=True)
            await asyncio.sleep(poll_interval)
            continue
        except Exception as e:
            print(f"Error fetching pending tasks: {e}", flush=True)
            await asyncio.sleep(poll_interval)
            continue

        if pending_tasks:
            print(f"Found {len(pending_tasks)} pending tasks. Executing simultaneously...", flush=True)

            # Create a list to hold our concurrent jobs
            concurrent_jobs = []

            for task in pending_tasks:
                # Fire the task into the background immediately
                job = asyncio.create_task(_process_one_task(controller, task))
                concurrent_jobs.append(job)

            # Wait for all tasks in this batch to finish before polling again
            # return_exceptions=True ensures if one completely crashes, the others still finish
            await asyncio.gather(*concurrent_jobs, return_exceptions=True)

        # Always sleep then poll again (keeps loop running after each cycle)
        await asyncio.sleep(poll_interval)


async def _process_one_task(controller: AdminDbContoller, task):
    if task.task_type == "create_vectors":
        print(f"Processing create_vectors task {task.id} for chatbot {task.chatbot_id}")
        await controller.update_background_task_status(
            task.id,
            Models.background_task_status.running,
        )
        try:
            task_data = task.task_data or {}
            urls = task_data.get("urls", [])
            files = task_data.get("files", [])
            await AppController.create_vectors_background_task(
                task.chatbot_id,
                urls,
                files,
                task.user_id,
            )
            await controller.update_background_task_status(
                task.id,
                Models.background_task_status.completed,
            )
            print(f"Task {task.id} completed successfully")
        except Exception as e:
            print(f"Error processing task {task.id}: {e}")
            await controller.update_background_task_status(
                task.id,
                Models.background_task_status.failed,
                str(e),
            )
    elif task.task_type == "get_products":
        print(f"Processing get_products task {task.id}")
        await controller.update_background_task_status(
            task.id,
            Models.background_task_status.running,
        )
        try:
            await AppController.get_products_background_task(
                task.chatbot_id,
                task.user_id,
                task.id,
            )
            await controller.update_background_task_status(
                task.id,
                Models.background_task_status.completed,
            )
            print(f"Task {task.id} (get_products) completed successfully")
        except Exception as e:
            print(f"Error processing get_products task {task.id}: {e}")
            await controller.update_background_task_status(
                task.id,
                Models.background_task_status.failed,
                str(e),
            )
    elif task.task_type == "query_expander_context":
        # Generate or refresh the store DNA (high-level store summary) for this store.
        # We treat task.user_id as the ecom_store.id / store_knowledge.store_id.
        print(f"Processing query_expander_context task {task.id}")
        await controller.update_background_task_status(
            task.id,
            Models.background_task_status.running,
        )
        try:
            await Services.generate_store_dna_from_titles(store_id=task.user_id)
            await controller.update_background_task_status(
                task.id,
                Models.background_task_status.completed,
            )
            print(f"Task {task.id} (query_expander_context) completed successfully")
        except Exception as e:
            print(f"Error processing query_expander_context task {task.id}: {e}")
            await controller.update_background_task_status(
                task.id,
                Models.background_task_status.failed,
                str(e),
            )
    else:
        print(f"Unknown task type '{task.task_type}' for task {task.id}")


async def main():
    """Initialize database and start polling for background tasks"""
    print("Initializing database...", flush=True)
    await init_db()
    print("Database initialized", flush=True)

    print("Starting background task worker (polling every 3 seconds)...", flush=True)
    try:
        await process_background_tasks()
    except Exception as e:
        print(f"Worker crashed: {e}", flush=True)
        raise


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(line_buffering=True) if hasattr(sys.stdout, "reconfigure") else None
    asyncio.run(main())
