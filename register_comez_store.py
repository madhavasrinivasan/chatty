import asyncio
import sys
from datetime import datetime, timezone

# Configure sys.path so we can import app modules directly
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core.config.db import init_db, close_db
from app.core.models.models import (
    ecom_store,
    chatbot_settings,
    ecom_store_type,
    users,
    background_tasks,
    background_task_type,
    background_task_status,
)


async def register_store(store_name: str, access_token: str, api_key: str):
    print("🔋 Connecting to database...")
    await init_db()

    try:
        # Alter columns to support longer tokens (up to 2048 characters)
        from tortoise.connection import connections
        conn = connections.get("default")
        await conn.execute_query("ALTER TABLE ecom_store ALTER COLUMN access_token TYPE VARCHAR(2048);")
        await conn.execute_query("ALTER TABLE ecom_store ALTER COLUMN refresh_token TYPE VARCHAR(2048);")
        print("✅ Database columns access_token and refresh_token expanded to VARCHAR(2048).")
    except Exception as e:
        print(f"⚠️ Warning altering database columns (may already be VARCHAR(2048)): {e}")

    try:
        # 1. Create a new user record for this store
        username = f"comez_{store_name.replace('-', '_')}"
        email = f"admin@{store_name}.com"
        
        user = await users.filter(username=username).first()
        if not user:
            import bcrypt
            hashed_pw = bcrypt.hashpw(b"comez123", bcrypt.gensalt()).decode("utf-8")
            print(f"➕ Creating new user: username={username}, email={email}")
            user = await users.create(
                username=username,
                email=email,
                password=hashed_pw,
                name=f"Comez {store_name.capitalize()} Admin",
                subscription_id=1
            )
        else:
            print(f"ℹ️ Found existing user: username={user.username} (ID: {user.id})")
        
        user_id = user.id
        
        # 2. Check if a chatbot_settings already exists for this user_id, or create one
        bot = await chatbot_settings.filter(user_id=user_id).first()
        if not bot:
            bot = await chatbot_settings.create(
                user_id=user_id,
                api_key=f"temp_key_{username}",
                template_json={},
                allowed_url=[]
            )

        # Generate a real AES-encrypted JWT token API key
        from app.core.services.shopify_service import encrypt_token
        from app.admin.controller.appcontroller import JWTService
        
        jwt_token = JWTService().generate_token({
            "user_id": user_id,
            "username": username,
            "chatbot_id": bot.id,
        })
        real_api_key = encrypt_token(jwt_token)
        bot.api_key = real_api_key
        await bot.save()
        print(f"🔑 Generated and saved valid encrypted API key.")

        # 3. Check if ecom_store record already exists for this store name, or create one
        store = await ecom_store.filter(store_name=store_name).first()
        if not store:
            print(f"➕ Creating new ecom_store registry for: {store_name} ({ecom_store_type.comez.value})")
            store = await ecom_store.create(
                user_id=user_id,
                chatbot_id=bot.id,
                store_id=f"comez_{store_name.replace('-', '_')}",
                store_name=store_name,
                access_token=access_token,
                store_type=ecom_store_type.comez,
                sync_status="idle",
                expires_at=None
            )
        else:
            print(f"ℹ️ Found existing store registry. Updating access token & store type...")
            store.access_token = access_token
            store.store_type = ecom_store_type.comez
            store.chatbot_id = bot.id
            await store.save()

        # 4. Create background tasks: catalog sync first, then store DNA
        print("\n⚙️ Queueing product sync + store DNA background tasks...")
        task = await background_tasks.create(
            chatbot_id=bot.id,
            user_id=store.id,
            task_type=background_task_type.get_products,
            status=background_task_status.pending,
            task_data={"store_id": store.id},
        )
        print(f"✅ Created pending get_products task ID: {task.id}")

        dna_task = await background_tasks.create(
            chatbot_id=bot.id,
            user_id=store.id,
            task_type=background_task_type.query_expander_context,
            status=background_task_status.pending,
            task_data={"store_id": store.id},
        )
        print(f"✅ Created pending query_expander_context (store DNA) task ID: {dna_task.id}")

        print("\n🎉 Success!")
        print(f"   Chatbot API Key : {bot.api_key}")
        print(f"   Store Name      : {store.store_name}")
        print(f"   Store Type      : {store.store_type}")
        print(f"   Access Token    : {store.access_token[:15]}...{store.access_token[-15:] if len(store.access_token) > 30 else ''}")
        print("\nSync trigger is now ready for this store in your admin panel / worker tasks!")

    except Exception as e:
        print(f"❌ Error during registration: {e}")
    finally:
        print("🔌 Closing database connection...")
        await close_db()


if __name__ == "__main__":
    # Get arguments
    if len(sys.argv) < 3:
        print("Usage: uv run register_comez_store.py <store_name> <access_token> [custom_api_key]")
        print("Example: uv run register_comez_store.py chatty-store-3 YOUR_COMEZ_ADMIN_BEARER_TOKEN")
        sys.exit(1)

    s_name = sys.argv[1].strip()
    token = sys.argv[2].strip()
    
    # Generate or use custom API Key
    custom_key = sys.argv[3].strip() if len(sys.argv) > 3 else f"comez_api_key_{s_name.replace('-', '_')}"

    asyncio.run(register_store(s_name, token, custom_key))
