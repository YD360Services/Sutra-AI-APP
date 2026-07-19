from fastapi import APIRouter, WebSocket, WebSocketDisconnect
import asyncio
import json
import websockets
import logging
import uuid
import time

from app.core.config import settings
from app.services.deepgram_service import DeepgramService
from app.cache.redis import redis_cache
from app.services.transcript_intelligence import transcript_engine
from app.services.context_orchestrator import context_orchestrator
from app.services.prompt_builder import prompt_builder
from app.db.database import get_db

router = APIRouter()
logger = logging.getLogger("copilotx.websockets")

# Dictionary to hold the task reference per session to allow cancellation of obsolete tasks
_active_analysis_tasks = {}
_last_run_time = {}

async def run_background_pipeline(session_id: str, transcript_text: str, client_ws: WebSocket):
    now = time.time()
    # Throttle: only run pipeline at most once every 400ms per session
    last_time = _last_run_time.get(session_id, 0)
    if now - last_time < 0.4:
        return

    # Check if there is a running task. Let it finish rather than thrashing/cancelling constantly
    if session_id in _active_analysis_tasks:
        old_task = _active_analysis_tasks[session_id]
        if not old_task.done():
            # Let the active task finish to write context to Redis
            return

    _last_run_time[session_id] = now
    # Start the new task
    task = asyncio.create_task(_process_pipeline(session_id, transcript_text, client_ws))
    _active_analysis_tasks[session_id] = task

async def _process_pipeline(session_id: str, transcript_text: str, client_ws: WebSocket):
    start_time = time.time()
    try:
        # Load previous state from Redis
        prev_state_name = "WAITING"
        cached_session = await redis_cache.get_session_state(session_id)
        
        # Skip processing if transcript is too short
        word_count = len(transcript_text.split())
        if word_count < 4:
            return

        if cached_session:
            prev_state_name = cached_session.get("state", "WAITING")
            
        # 1. Transcript Analysis (Synchronous call)
        t_start = time.time()
        analysis = transcript_engine.analyze(
            raw_transcript=transcript_text,
            previous_state=prev_state_name,
            pause_duration=0.0
        )
        t_duration = time.time() - t_start
        
        # 2. Context Orchestration (REDIS-ONLY FAST PATH)
        c_start = time.time()
        context = None
        
        # If cache exists and has metadata/context cached, read from Redis directly (0 DB queries)
        if cached_session and cached_session.get("metadata_loaded"):
            context = {
                "resume_context": cached_session.get("resume_context", "None loaded."),
                "knowledge_context": cached_session.get("knowledge_context", "None loaded."),
                "jd_context": cached_session.get("jd_context", "None loaded."),
                "previous_context": cached_session.get("previous_context", "None."),
                "reasoning_focus": cached_session.get("reasoning_focus", "General technical review and validation.")
            }
        else:
            # Fallback/First Run: Load from Postgres once and cache in Redis session state
            async for db in get_db():
                user_id = None
                session_jd_id = None
                resume_content = None
                knowledge_content = None
                
                try:
                    from app.db.models import Session
                    is_valid_uuid = False
                    session_uuid = None
                    if isinstance(session_id, uuid.UUID):
                        is_valid_uuid = True
                        session_uuid = session_id
                    elif isinstance(session_id, str):
                        try:
                            session_uuid = uuid.UUID(session_id)
                            is_valid_uuid = True
                        except ValueError:
                            pass
                            
                    if is_valid_uuid and session_uuid:
                        db_session = await db.get(Session, session_uuid)
                        if db_session:
                            user_id = db_session.user_id
                            session_jd_id = db_session.job_description_id
                            resume_content = db_session.resume_content
                            knowledge_content = db_session.knowledge_content
                except Exception as db_err:
                    logger.warning(f"Database error reading session in pipeline: {db_err}")
                    
                context = await context_orchestrator.prepare_context(
                    session_id=uuid.UUID(session_id) if isinstance(session_id, str) and len(session_id) == 36 else None,
                    user_id=user_id,
                    db=db,
                    keywords=analysis["keywords"],
                    technologies=analysis["technologies"],
                    resume_content=resume_content,
                    knowledge_content=knowledge_content,
                    session_jd_id=session_jd_id
                )
                
                # Update cached session state with metadata and context so future runs bypass DB
                if cached_session is None:
                    cached_session = {}
                cached_session.update({
                    "metadata_loaded": True,
                    "resume_context": context["resume_context"],
                    "knowledge_context": context["knowledge_context"],
                    "previous_context": context["previous_context"],
                    "reasoning_focus": context["reasoning_focus"]
                })
                await redis_cache.set_session_state(session_id, cached_session)
                break
                
        c_duration = time.time() - c_start
        
        # 3. As Assembled Prompt and Cache Update
        p_start = time.time()
        await prompt_builder.update_session_prompt(session_id, analysis, context, transcript_text)
        p_duration = time.time() - p_start
        
        # Send live state updates back to browser socket client
        try:
            await client_ws.send_json({
                "type": "pipeline_update",
                "state": analysis["state"],
                "prediction": analysis["prediction"],
                "ready_for_answer": analysis["ready_for_answer"],
                "confidence": analysis["confidence"],
                "intent": analysis["intent"],
                "category": analysis["category"],
                "technologies": analysis["technologies"]
            })
        except Exception as ws_err:
            logger.warning(f"Failed to send pipeline_update to client WS: {ws_err}")
            
        total_duration = time.time() - start_time
        logger.info(
            f"[Pipeline] Assembled background prompt for session {session_id} in {total_duration:.4f}s. "
            f"Analysis: {t_duration*1000:.2f}ms, Context: {c_duration*1000:.2f}ms, Prompt: {p_duration*1000:.2f}ms"
        )
    except asyncio.CancelledError:
        logger.debug(f"Analysis task for session {session_id} was cancelled.")
    except Exception as e:
        logger.error(f"Error running background pipeline: {e}", exc_info=True)

async def run_websocket_proxy(client_ws: WebSocket, session_id: str):
    await client_ws.accept()
    
    dg_url = DeepgramService.get_websocket_url()
    client_id = str(uuid.uuid4())
    # Track this active socket connection in Redis
    await redis_cache.track_websocket(session_id, client_id, register=True)

    use_speechmatics = True # Temporary test mode as requested
    
    if use_speechmatics:
        sm_url = "wss://eu.rt.speechmatics.com/v2"
        headers = {
            "Authorization": f"Bearer {settings.SPEECHMATICS_API_KEY}"
        }
        await client_ws.send_json({"type": "status", "message": "Connecting to Speechmatics"})
        try:
            async with websockets.connect(
                sm_url,
                additional_headers=headers,
                max_size=10_000_000,
            ) as sm_ws:
                logger.info(f"Connected to Speechmatics WebSocket for session {session_id} successfully.")
                
                # Send StartRecognition configuration message
                start_msg = {
                    "message": "StartRecognition",
                    "audio_format": {
                        "type": "file"
                    },
                    "transcription_config": {
                        "language": "en",
                        "operating_point": "enhanced",
                        "enable_partials": True
                    }
                }
                await sm_ws.send(json.dumps(start_msg))
                await client_ws.send_json({"type": "status", "message": "Listening"})
                
                async def browser_to_speechmatics():
                    try:
                        while True:
                            audio_chunk = await client_ws.receive_bytes()
                            await sm_ws.send(audio_chunk)
                    except WebSocketDisconnect:
                        pass
                    except Exception as e:
                        logger.debug(f"Browser connection closed or error: {e}")
                        
                async def speechmatics_to_browser():
                    async for raw in sm_ws:
                        try:
                            data = json.loads(raw)
                        except Exception:
                            continue
                            
                        msg_type = data.get("message")
                        if msg_type in ("AddTranscript", "AddPartialTranscript"):
                            transcript = data.get("metadata", {}).get("transcript", "").strip()
                            if not transcript:
                                continue
                                
                            is_final = msg_type == "AddTranscript"
                            logger.info(f"[Speechmatics] Session {session_id} - Transcript: '{transcript}', is_final={is_final}")
                            
                            current = await redis_cache.get_transcript(session_id) or ""
                            updated = f"{current} {transcript}".strip()
                            
                            if is_final:
                                await redis_cache.set_transcript(session_id, updated)
                                
                            asyncio.create_task(run_background_pipeline(session_id, updated, client_ws))
                            
                            await client_ws.send_json({
                                "type": "transcript",
                                "text": transcript,
                                "is_final": is_final,
                            })
                            
                await asyncio.gather(browser_to_speechmatics(), speechmatics_to_browser())
                return
        except Exception as sm_err:
            logger.error(f"Speechmatics connection failed: {sm_err}. Falling back to Deepgram...")
            await client_ws.send_json({"type": "status", "message": "Speechmatics failed, falling back to Deepgram"})

    try:
        async with websockets.connect(
            dg_url,
            additional_headers=DeepgramService.get_auth_headers(),
            max_size=10_000_000,
        ) as dg_ws:
            logger.info(f"Connected to Deepgram WebSocket for session {session_id} successfully.")
            await client_ws.send_json({"type": "status", "message": "Listening"})

            async def browser_to_deepgram():
                try:
                    while True:
                        audio_chunk = await client_ws.receive_bytes()
                        await dg_ws.send(audio_chunk)
                except WebSocketDisconnect:
                    pass
                except Exception as e:
                    logger.debug(f"Browser connection closed or error: {e}")

            async def deepgram_to_browser():
                async for raw in dg_ws:
                    try:
                        data = json.loads(raw)
                    except Exception:
                        continue

                    transcript = (
                        data.get("channel", {})
                        .get("alternatives", [{}])[0]
                        .get("transcript", "")
                        .strip()
                    )
                    if not transcript:
                        continue

                    is_final = bool(data.get("is_final") or data.get("speech_final"))
                    logger.info(f"[Deepgram] Session {session_id} - Transcript: '{transcript}', is_final={is_final}")
                    
                    current = await redis_cache.get_transcript(session_id) or ""
                    updated = f"{current} {transcript}".strip()
                    
                    if is_final:
                        await redis_cache.set_transcript(session_id, updated)

                    # Run intent prediction & context retrieval pipeline asynchronously without blocking
                    asyncio.create_task(run_background_pipeline(session_id, updated, client_ws))

                    await client_ws.send_json({
                        "type": "transcript",
                        "text": transcript,
                        "is_final": is_final,
                    })

            await asyncio.gather(browser_to_deepgram(), deepgram_to_browser())
            
    except WebSocketDisconnect:
        logger.info(f"WebSocket client disconnected for session {session_id} cleanly.")
    except Exception as exc:
        logger.error(f"Deepgram proxy failed: {exc}")
        try:
            await client_ws.send_json({"type": "status", "message": f"Deepgram error: {str(exc)}"})
            await client_ws.close()
        except Exception:
            pass
    finally:
        await redis_cache.track_websocket(session_id, client_id, register=False)

# Backward-compatible endpoint (current UI)
@router.websocket("/ws/deepgram")
async def legacy_deepgram_ws(client_ws: WebSocket):
    session_id = "legacy-session-id"
    await run_websocket_proxy(client_ws, session_id)

# Scalable path endpoint (future integrations)
@router.websocket("/ws/audio/{session_id}")
async def session_deepgram_ws(client_ws: WebSocket, session_id: str):
    await run_websocket_proxy(client_ws, session_id)
