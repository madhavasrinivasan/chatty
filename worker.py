import asyncio
import os

# Set environment variable before any imports (macOS fork() safety)
os.environ['OBJC_DISABLE_INITIALIZE_FORK_SAFETY'] = 'YES'

from app.core.config.db import init_db
from app.core.models.dbontrollers.admindbcontroller import AdminDbContoller
from app.admin.controller.appcontroller import AppController
from app.core import models as Models


async def process_background_tasks():
    """Poll database every 3 seconds for pending background tasks and process them"""
    controller = AdminDbContoller()
    
    while True:
        try:
            # Get pending tasks
            pending_tasks = await controller.get_pending_background_tasks()
            
            if pending_tasks :
                for task in pending_tasks:
                    if task.task_type == "create_vectors":
                        print(f"Processing background task {task.id} for chatbot {task.chatbot_id}")
                        
                        # Update status to running
                        await controller.update_background_task_status(
                            task.id, 
                            Models.background_task_status.running
                        )
                        
                        try:
                            task_data = task.task_data or {}
                            urls = task_data.get("urls", [])
                            files = task_data.get("files", [])
                            
                            # Run the actual task
                            await AppController.create_vectors_background_task(
                                task.chatbot_id,
                                urls,
                                files,
                                task.user_id
                            )
                            
                            # Mark as completed
                            await controller.update_background_task_status(
                                task.id,
                                Models.background_task_status.completed
                            )
                            print(f"Task {task.id} completed successfully")
                            
                        except Exception as e:
                            print(f"Error processing task {task.id}: {e}")
                            # Mark as failed
                            await controller.update_background_task_status(
                                task.id,
                                Models.background_task_status.failed,
                                str(e)
                            )
                    elif task.task_type == "get_products":
                        print(f"Processing get_products task {task.id}")
                        # Add actual task processing logic here if required
                        try:
                            await controller.update_background_task_status(
                                task.id, 
                                Models.background_task_status.running
                            )
                            await AppController.get_products_background_task(
                                task.chatbot_id,
                                task.user_id,
                                task.id
                            )
                        except Exception as e:
                            print(f"Error processing get_products task {task.id}: {e}")
                            await controller.update_background_task_status(
                                task.id,
                                Models.background_task_status.failed,
                                str(e)
                            )
                    else:
                        print(f"Unknown task type '{task.task_type}' for task {task.id}")
            # Wait 3 seconds before next poll
            await asyncio.sleep(3)
            
        except Exception as e:
            print(f"Error in background task processor: {e}")
            await asyncio.sleep(3)


async def main():
    """Initialize database and start polling for background tasks"""
    print("Initializing database...")
    await init_db()
    print("Database initialized")
    
    print("Starting background task worker (polling every 3 seconds)...")
    await process_background_tasks()


if __name__ == "__main__":
    asyncio.run(main())
