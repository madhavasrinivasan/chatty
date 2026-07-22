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
                file_extension = mimetypes.guess_extension(content_type) or ""
                name_lower = file_name.lower()
                is_pdf = (
                    file_extension == ".pdf"
                    or name_lower.endswith(".pdf")
                    or content_type in ("application/pdf", "application/x-pdf")
                )
                
                # Check if file is PDF and size is valid
                if not is_pdf:
                    raise ApplicationError.BadRequest(f"Invalid file type. Only PDF files are allowed. File: {file_name}")
                file_extension = ".pdf"
                
                # UploadFile.size can be None depending on client; fall back to reading length after write if needed
                declared_size = getattr(file, "size", None)
                if declared_size is not None and declared_size > max_size:
                    raise ApplicationError.BadRequest(f"File size exceeds maximum allowed size ({max_size} bytes). File: {file_name}")
                
                file_path = os.path.join(upload_dir, file_name)
                total_written = 0
                async with aiofiles.open(file_path, "wb") as out_file:
                    while True:
                        chunk = await file.read(20 *1024 * 1024) # 20MB chunk size
                        if not chunk:
                            break
                        total_written += len(chunk)
                        if total_written > max_size:
                            await out_file.close()
                            try:
                                os.remove(file_path)
                            except OSError:
                                pass
                            raise ApplicationError.BadRequest(
                                f"File size exceeds maximum allowed size ({max_size} bytes). File: {file_name}"
                            )
                        await out_file.write(chunk)
                
                file_dict: dict = {
                    "file_path": file_path,
                    "file_name": file_name,
                    "file_extension": file_extension,
                }
                file_paths.append(file_dict)
            return file_paths
        except ApplicationError:
            raise
        except Exception as e:
            print(f"error uploading file: {e}")
            raise ApplicationError.SomethingWentWrong("Error uploading file")

    async def upload_image(self, file: UploadFile) -> str:
        return await self.upload_and_compress_image(file)

    async def upload_and_compress_image(self, file: UploadFile) -> str:
        try:
            upload_dir = os.path.join("Assets", "Images")
            os.makedirs(upload_dir, exist_ok=True)
            
            if not file or not file.filename:
                raise ApplicationError.BadRequest("No file uploaded")
                
            ext = os.path.splitext(file.filename)[1].lower()
            valid_exts = [".png", ".jpg", ".jpeg", ".svg", ".webp"]
            if ext not in valid_exts:
                raise ApplicationError.BadRequest(f"Invalid file type. Only PNG, SVG, JPEG, and WebP are allowed. File: {file.filename}")
            
            max_size = 1 * 1024 * 1024  # 1MB limit
            
            # Read all file bytes
            file_bytes = await file.read()
            size = len(file_bytes)
                
            if size > max_size:
                raise ApplicationError.BadRequest(f"Image size exceeds maximum allowed size (1MB). File: {file.filename}")
            
            file_name = f"{int(time.time())}_{file.filename}"
            file_path = os.path.join(upload_dir, file_name)
            
            # Compress image if not SVG
            if ext != ".svg":
                try:
                    import io
                    from PIL import Image
                    img = Image.open(io.BytesIO(file_bytes))
                    format_to_save = img.format or "JPEG"
                    
                    if img.mode in ("RGBA", "LA") and format_to_save in ("JPEG", "JPG"):
                        background = Image.new("RGBA", img.size, (255, 255, 255))
                        alpha_composite = Image.alpha_composite(background, img)
                        img = alpha_composite.convert("RGB")
                    
                    out_io = io.BytesIO()
                    if format_to_save in ("JPEG", "JPG"):
                        img.save(out_io, format="JPEG", quality=75, optimize=True)
                    elif format_to_save == "PNG":
                        img.save(out_io, format="PNG", optimize=True)
                    elif format_to_save == "WEBP":
                        img.save(out_io, format="WEBP", quality=75, method=6)
                    else:
                        img.save(out_io, format="WEBP", quality=75)
                    
                    compressed_bytes = out_io.getvalue()
                    if len(compressed_bytes) < len(file_bytes):
                        file_bytes = compressed_bytes
                except Exception as comp_err:
                    print(f"Error compressing image: {comp_err}")
            
            # Write bytes to disk
            async with aiofiles.open(file_path, "wb") as out_file:
                await out_file.write(file_bytes)
                
            return f"/assets/Images/{file_name}"
        except Exception as e:
            if isinstance(e, ApplicationError):
                raise e
            print(f"error uploading image: {e}")
            raise ApplicationError.SomethingWentWrong("Error uploading image")