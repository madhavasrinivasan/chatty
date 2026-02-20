from typing import List
import mimetypes
from fastapi import UploadFile
from app.core.schema.applicationerror import ApplicationError
from app.core.schema.schemarespone import APIResponse
from app.core.services.jwt import JWTService
import time 
import os
from app.core.config.config import Settings
import aiofiles
import asyncio
import shutil


class FileHandler:
    def __init__(self):
        self.settings = Settings() 

    async def upload_file(self, files: List[UploadFile]):
        try:
            file_paths = []
            max_size = self.settings.file_upload_max_size
            
            # Create the directory if it doesn't exist
            upload_dir = self.settings.file_upload_directory_pdf
            os.makedirs(upload_dir, exist_ok=True)
            
            if not files:
                return file_paths
            
            for file in files:
                if not file or not file.filename:
                    continue
                    
                file_name = file.filename
                # Handle None content_type
                content_type = file.content_type or ""
                file_extension = mimetypes.guess_extension(content_type)
                
                # Check if file is PDF and size is valid
                if file_extension != ".pdf":
                    raise ApplicationError.BadRequest(f"Invalid file type. Only PDF files are allowed. File: {file_name}")
                
                if file.size > max_size:
                    raise ApplicationError.BadRequest(f"File size exceeds maximum allowed size ({max_size} bytes). File: {file_name}")
                
                file_path = os.path.join(upload_dir, file_name)
                async with aiofiles.open(file_path, "wb") as out_file:
                    while True:
                        chunk = await file.read(20 *1024 * 1024) # 20MB chunk size
                        if not chunk:
                            break
                        await out_file.write(chunk)
                
                file_dict: dict = {
                    "file_path": file_path,
                    "file_name": file_name,
                    "file_extension": file_extension,
                }
                file_paths.append(file_dict)
            return file_paths
        except Exception as e:
            print(f"error uploading file: {e}")
            raise ApplicationError.SomethingWentWrong("Error uploading file")