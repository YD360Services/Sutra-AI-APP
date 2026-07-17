from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional
import uuid
import io

try:
    import pypdf
except ImportError:
    pypdf = None

try:
    import docx
except ImportError:
    docx = None

from app.db.database import get_db
from app.db.repositories import ResumeRepository
from app.schemas.resume import ResumeCreate, ResumeResponse
from app.core.user_utils import normalize_user_id

router = APIRouter()

@router.post("/resumes", response_model=ResumeResponse)
async def upload_resume(
    payload: ResumeCreate,
    user_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    normalized_user_id = normalize_user_id(user_id)
    repo = ResumeRepository(db)
    resume = await repo.create(
        user_id=normalized_user_id,
        file_name=payload.file_name,
        parsed_content=payload.parsed_content
    )
    return resume

def extract_text_from_pdf(file_bytes: bytes) -> str:
    if not pypdf:
        raise ImportError("The 'pypdf' package is not installed. Please run 'pip install pypdf' in your active environment.")
    pdf = pypdf.PdfReader(io.BytesIO(file_bytes))
    text = ""
    for page in pdf.pages:
        text += page.extract_text() or ""
    return text.strip()

def extract_text_from_docx(file_bytes: bytes) -> str:
    if not docx:
        raise ImportError("The 'python-docx' package is not installed. Please run 'pip install python-docx' in your active environment.")
    doc = docx.Document(io.BytesIO(file_bytes))
    text = ""
    for para in doc.paragraphs:
        text += para.text + "\n"
    return text.strip()

@router.post("/resumes/upload", response_model=ResumeResponse)
async def upload_resume_file(
    file: UploadFile = File(...),
    user_id: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db)
):
    normalized_user_id = normalize_user_id(user_id)
    file_bytes = await file.read()
    file_name = file.filename or "resume.pdf"
    
    parsed_content = ""
    lower_name = file_name.lower()
    
    try:
        if lower_name.endswith(".pdf"):
            parsed_content = extract_text_from_pdf(file_bytes)
        elif lower_name.endswith((".docx", ".doc")):
            try:
                parsed_content = extract_text_from_docx(file_bytes)
            except Exception:
                parsed_content = file_bytes.decode("utf-8", errors="ignore")
        else:
            parsed_content = file_bytes.decode("utf-8", errors="ignore")
    except Exception as parse_err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to parse resume file: {str(parse_err)}"
        )
    finally:
        await file.close()
        
    if not parsed_content.strip():
        parsed_content = f"Uploaded resume file: {file_name}. Text extraction returned no content."

    repo = ResumeRepository(db)
    resume = await repo.create(
        user_id=normalized_user_id,
        file_name=file_name,
        parsed_content=parsed_content
    )

    try:
        from app.cache.redis import redis_cache
        if resume and resume.id:
            await redis_cache.set_resume(str(resume.id), parsed_content)
    except Exception as cache_err:
        logger.warning(f"Failed to cache uploaded resume: {cache_err}")

    return resume

@router.get("/resumes", response_model=List[ResumeResponse])
async def list_resumes(
    user_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    normalized_user_id = normalize_user_id(user_id)
    repo = ResumeRepository(db)
    resumes = await repo.list_by_user(user_id=normalized_user_id)
    return resumes

@router.patch("/resumes/{resume_id}/activate", response_model=ResumeResponse)
async def activate_resume(
    resume_id: uuid.UUID,
    user_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    repo = ResumeRepository(db)
    resume = await repo.activate(user_id=user_id, resume_id=resume_id)
    if not resume:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resume not found"
        )
    return resume

@router.delete("/resumes/{resume_id}")
async def delete_resume(
    resume_id: uuid.UUID,
    db: AsyncSession = Depends(get_db)
):
    repo = ResumeRepository(db)
    deleted = await repo.delete(resume_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resume not found"
        )
    return {"status": "success", "message": "Resume deleted"}
