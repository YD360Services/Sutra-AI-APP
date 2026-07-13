from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional
import uuid
import json

from app.db.database import get_db
from app.db.repositories import KnowledgeRepository
from app.schemas.knowledge import KnowledgeCreate, KnowledgeResponse

router = APIRouter()


@router.post("/knowledge", response_model=KnowledgeResponse)
async def create_knowledge(
    payload: KnowledgeCreate,
    user_id: Optional[uuid.UUID] = None,
    db: AsyncSession = Depends(get_db)
):
    repo = KnowledgeRepository(db)
    doc = await repo.create(
        user_id=user_id,
        name=payload.document_name,
        doc_type=payload.document_type,
        content=payload.content
    )

    try:
        from app.cache.redis import redis_cache
        if doc and doc.id:
            await redis_cache.set_cached_item(f"knowledge:{doc.id}", payload.content)
    except Exception as cache_err:
        import logging
        logger = logging.getLogger("copilotx.knowledge")
        logger.warning(f"Failed to cache uploaded knowledge document: {cache_err}")

    return doc

@router.get("/knowledge", response_model=List[KnowledgeResponse])
async def list_knowledge(
    user_id: Optional[uuid.UUID] = None,
    db: AsyncSession = Depends(get_db)
):
    repo = KnowledgeRepository(db)
    docs = await repo.list_by_user(user_id=user_id)
    return docs

@router.delete("/knowledge/{document_id}")
async def delete_knowledge(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db)
):
    repo = KnowledgeRepository(db)
    deleted = await repo.delete(document_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found"
        )
    return {"status": "success", "message": "Document deleted"}

@router.post("/knowledge/upload", response_model=KnowledgeResponse)
async def upload_knowledge_file(
    file: UploadFile = File(...),
    document_type: str = Form("document"),  # "document" or "prompt"
    user_id: Optional[uuid.UUID] = Form(None),
    db: AsyncSession = Depends(get_db)
):
    file_bytes = await file.read()
    file_name = file.filename or "document.txt"
    
    parsed_content = ""
    lower_name = file_name.lower()
    
    try:
        if lower_name.endswith(".pdf"):
            from app.api.resumes import extract_text_from_pdf
            parsed_content = extract_text_from_pdf(file_bytes)
        elif lower_name.endswith((".docx", ".doc")):
            from app.api.resumes import extract_text_from_docx
            try:
                parsed_content = extract_text_from_docx(file_bytes)
            except Exception:
                parsed_content = file_bytes.decode("utf-8", errors="ignore")
        else:
            parsed_content = file_bytes.decode("utf-8", errors="ignore")
    except Exception as parse_err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to parse document file: {str(parse_err)}"
        )
    finally:
        await file.close()
        
    if not parsed_content.strip():
        parsed_content = f"Uploaded document file: {file_name}. Text extraction returned no content."

    repo = KnowledgeRepository(db)
    doc = await repo.create(
        user_id=user_id,
        name=file_name,
        doc_type=document_type,
        content=parsed_content
    )

    try:
        from app.cache.redis import redis_cache
        if doc and doc.id:
            doc_json = json.dumps({
                "name": file_name,
                "type": document_type,
                "content": parsed_content
            })
            await redis_cache.set_cached_item(f"knowledge:{doc.id}", doc_json)
    except Exception as cache_err:
        import logging
        logger = logging.getLogger("copilotx.knowledge")
        logger.warning(f"Failed to cache uploaded knowledge document: {cache_err}")

    return doc

