from ast import List
import mimetypes
from fastapi import UploadFile, File
from app.core.schema.applicationerror import ApplicationError
from app.core.schema.schemarespone import APIResponse
from app.core.services.jwt import JWTService
import time 
import os
from app.core.config.config import Settings
import aiofiles
import asyncio



class FileHandler:
    def __init__(self):
        self.settings = Settings() 

    import shutil

    async def upload_file(self, files: List[UploadFile] = File(...)):
        try:
            file_paths = []
            max_size = self.settings.file_upload_max_size
            for file in files:
                file_name = file.filename
                file_extension = mimetypes.guess_extension(file.content_type)
                if file_extension == ".pdf" and file.size <= max_size:
                    file_path = os.path.join(self.settings.file_upload_directory_pdf, file_name)
                    async with aiofiles.open(file_path, "wb") as out_file:
                        while True:
                            chunk = await file.read(1024 * 1024)
                            if not chunk:
                                break
                            await out_file.write(chunk)
                        file_dict:dict = {
                            "file_path": file_path,
                            "file_name": file_name,
                            "file_extension": file_extension,
                        }
                    file_paths.append(file_dict)
                else:
                    raise ApplicationError.BadRequest("Invalid file type or size")
            return file_paths
        except Exception as e:
            print(f"error uploading file: {e}")
            raise ApplicationError.SomethingWentWrong("Error uploading file")