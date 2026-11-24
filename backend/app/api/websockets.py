from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from typing import Optional, Dict, Any, List
import asyncio
import json
import websockets
import logging
import uuid
import time

from app.core.config import settings
from app.services.deepgram_service import DeepgramService
from app.services.transcript_vocab import correct_transcript, is_software_role, keyterms
from app.cache.redis import redis_cache
from app.services.transcript_intelligence import transcript_engine
from app.services.context_orchestrator import context_orchestrator
from app.services.prompt_builder import prompt_builder
from app.db.database import get_db

router = APIRouter()
logger = logging.getLogger("roundmate.websockets")

# Dictionary to hold the task reference per session to allow cancellation of obsolete tasks
_active_analysis_tasks: dict = {}
_active_generation_tasks: dict = {}
_generation_versions: dict = {}
_outbound_queues: dict = {}  # session_id -> asyncio.Queue for single-writer serialization
_last_run_time: dict = {}
# Per-session last transcript timestamp for pause duration detection
_last_transcript_time: dict = {}

async def send_ws_event(session_id: str, payload: dict, client_ws: Optional[WebSocket] = None):
    """
    Serializes all outbound WebSocket events through a dedicated bounded queue,
    preventing socket send collisions, interleaved frames, and race conditions.
    """
    queue = _outbound_queues.get(session_id)
    if queue:
        try:
            queue.put_nowait(payload)
            return
        except asyncio.QueueFull:
            logger.warning(f"[WS:Backpressure] Bounded queue full for {session_id}. Dropping non-critical frame: {payload.get('event')}")
            return
    if client_ws:
        try:
            await client_ws.send_json(payload)
        except Exception as e:
            logger.debug(f"[WS:Send] Fallback send error: {e}")

async def stream_answer_over_websocket(
    session_id: str,
    question: str,
    model: Optional[str],
    client_ws: WebSocket,
    prediction_id: str,
    version: int,
    source_type: str = "transcript",
    answer_id: Optional[str] = None
):
    """
    Streams answer tokens directly over the persistent WebSocket connection
    gated by immutable answer_id, prediction_id and distributed generation_version guards.
    """
    answer_id = answer_id or str(uuid.uuid4())
    if _generation_versions.get(session_id, 0) != version:
        logger.info(f"[WS] Aborting obsolete generation version {version} for session {session_id}")
        return

    start_time = time.time()
    accumulated_chunks = []
    seq = 0

    try:
        from app.db.database import SessionLocal
        from app.services.context_service import build_session_context
        from app.services.ai_service import stream_llm

        async with SessionLocal() as db_session:
            sid = uuid.UUID(session_id) if len(session_id) == 36 else None
            ctx_dict = await build_session_context(session_id=sid, latest_question=question, db=db_session)
            sys_prompt = ctx_dict.get("sys_prompt") or ""
            context_prompt = ctx_dict.get("context_prompt") or question
            resolved_model = model or ctx_dict.get("model") or "gemini-3.8-flash"

        # Emit answer.started event through serialized queue
        await send_ws_event(session_id, {
            "protocol_version": "2.0",
            "event_id": str(uuid.uuid4()),
            "event": "answer.started",
            "session_id": session_id,
            "answer_id": answer_id,
            "prediction_id": prediction_id,
            "generation_version": version,
            "model": resolved_model
        }, client_ws=client_ws)

        async for chunk in stream_llm(prompt=context_prompt, system_prompt=sys_prompt, model=resolved_model):
            # STALE TOKEN GUARD: if prediction was superseded or cancelled mid-stream, drop immediately
            if _generation_versions.get(session_id, 0) != version:
                logger.info(f"[WS] Dropping stale token for superseded version {version} in session {session_id}")
                return

            seq += 1
            accumulated_chunks.append(chunk)
            await send_ws_event(session_id, {
                "protocol_version": "2.0",
                "event_id": str(uuid.uuid4()),
                "event": "answer.chunk",
                "session_id": session_id,
                "answer_id": answer_id,
                "prediction_id": prediction_id,
                "generation_version": version,
                "seq": seq,
                "text": chunk
            }, client_ws=client_ws)

        duration_ms = (time.time() - start_time) * 1000
        full_answer = "".join(accumulated_chunks)

        # Emit answer.completed event
        await send_ws_event(session_id, {
            "protocol_version": "2.0",
            "event_id": str(uuid.uuid4()),
            "event": "answer.completed",
            "session_id": session_id,
            "answer_id": answer_id,
            "prediction_id": prediction_id,
            "generation_version": version,
            "full_answer": full_answer,
            "tokens": max(1, len(full_answer) // 4),
            "duration_ms": duration_ms
        }, client_ws=client_ws)

        # Decoupled persistence: save finalized QA exchange to PostgreSQL WITH DISTRIBUTED ATOMIC VERSION GUARD
        if full_answer:
            async def _persist_qa(target_version: int, target_prediction_id: str, target_answer_id: str):
                try:
                    # ATOMIC CHECK 1: Ensure Redis generation version is STILL current across all workers
                    active_redis_v = await redis_cache.get_generation_version(session_id)
                    if (active_redis_v > 0 and active_redis_v != target_version) or _generation_versions.get(session_id, 0) != target_version:
                        logger.info(f"[WS:Persist] Multi-worker atomic guard rejected stale QA persist v{target_version} (redis_active={active_redis_v})")
                        return

                    from app.db.database import SessionLocal
                    from app.db.repositories import QARepository
                    async with SessionLocal() as db:
                        # ATOMIC CHECK 2: Re-verify right before committing transaction
                        active_redis_v2 = await redis_cache.get_generation_version(session_id)
                        if (active_redis_v2 > 0 and active_redis_v2 != target_version) or _generation_versions.get(session_id, 0) != target_version:
                            logger.info(f"[WS:Persist] Multi-worker race detected! Discarding commit for superseded v{target_version}")
                            return

                        qa_repo = QARepository(db)
                        await qa_repo.create(
                            session_id=uuid.UUID(session_id) if len(session_id) == 36 else uuid.uuid4(),
                            question=question,
                            answer=full_answer,
                            source_type=source_type
                        )
                        await db.commit()
                        logger.info(f"[WS:Persist] Atomically committed QA exchange for answer {target_answer_id} v{target_version}")
                except Exception as p_err:
                    logger.warning(f"Error persisting QA to DB: {p_err}")

            asyncio.create_task(_persist_qa(version, prediction_id, answer_id))

    except asyncio.CancelledError:
        logger.info(f"[WS] Generation task {prediction_id} v{version} cancelled successfully.")
    except Exception as e:
        logger.error(f"[WS] Error in stream_answer_over_websocket: {e}", exc_info=True)
        try:
            await send_ws_event(session_id, {
                "event": "error",
                "code": "GENERATION_FAILED",
                "message": str(e),
                "session_id": session_id
            }, client_ws=client_ws)
        except Exception:
            pass


async def run_background_pipeline(session_id: str, transcript_text: str, client_ws: WebSocket, pause_duration: float = 0.0):
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
        # Clean up completed task to prevent memory leak
        del _active_analysis_tasks[session_id]

    _last_run_time[session_id] = now
    # Start the new task
    task = asyncio.create_task(_process_pipeline(session_id, transcript_text, client_ws, pause_duration))
    _active_analysis_tasks[session_id] = task

    # Auto-remove from map when done to prevent unbounded memory growth
    def _cleanup_task(t: asyncio.Task):
        _active_analysis_tasks.pop(session_id, None)
    task.add_done_callback(_cleanup_task)

async def _process_pipeline(session_id: str, transcript_text: str, client_ws: WebSocket, pause_duration: float = 0.0):
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
            pause_duration=pause_duration
        )
        t_duration = time.time() - t_start
        
        # 2. Context Orchestration (REDIS-ONLY FAST PATH)
        c_start = time.time()
        context = None

        # If cache exists, has metadata loaded, AND jd_context is present, use Redis directly (0 DB queries)
        # Note: jd_context guard prevents the silent "None loaded." fallback from a partial first-run write
        if cached_session and cached_session.get("metadata_loaded") and cached_session.get("jd_context"):
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
                    # FIX #1: jd_context was missing — silently fell back to "None loaded." every session
                    "jd_context": context["jd_context"],
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

async def _session_is_software(session_id: str) -> bool:
    """Role-aware vocabulary needs the session's role; the web app only sends the session id."""
    try:
        sid = uuid.UUID(str(session_id))
    except (ValueError, TypeError):
        return False
    try:
        from app.db import database
        from app.db.models import Session as SessionModel
        if database.SessionLocal is None:
            return False
        async with database.SessionLocal() as db:
            s = await db.get(SessionModel, sid)
            return bool(s) and is_software_role(s.role_name, s.session_name)
    except Exception as e:
        logger.debug(f"Role lookup failed for session {session_id}: {e}")
        return False


async def run_websocket_proxy(client_ws: WebSocket, session_id: str):
    await client_ws.accept()

    # Dedicated Single-Writer Outbound Queue with Bounded Backpressure
    outbound_queue = asyncio.Queue(maxsize=150)
    _outbound_queues[session_id] = outbound_queue

    async def dedicated_writer_loop():
        """Single dedicated coroutine to sequentially write all frames to client_ws with bounded backpressure."""
        try:
            while True:
                payload = await outbound_queue.get()
                if payload is None:
                    break
                try:
                    await client_ws.send_json(payload)
                except Exception as ws_write_err:
                    logger.debug(f"[WS:Writer] Write error: {ws_write_err}")
                    break
                finally:
                    outbound_queue.task_done()
        except asyncio.CancelledError:
            pass

    writer_task = asyncio.create_task(dedicated_writer_loop())
    
    # Extract language from query params (e.g. ?language=hi or ?lang=te)
    requested_lang = (
        client_ws.query_params.get('language') or 
        client_ws.query_params.get('lang') or 
        'en'
    ).strip()

    software = await _session_is_software(session_id)
    dg_url = DeepgramService.get_websocket_url(language=requested_lang, keyterms=keyterms(software))
    client_id = str(uuid.uuid4())
    # Track this active socket connection in Redis
    await redis_cache.track_websocket(session_id, client_id, register=True)

    # Client can force provider via query parameter ?provider=deepgram or ?provider=speechmatics
    requested_provider = (client_ws.query_params.get('provider') or client_ws.query_params.get('engine') or '').lower()
    if requested_provider == 'deepgram':
        use_speechmatics = False
    elif requested_provider in ('speechmatics', 'sm'):
        use_speechmatics = True
    else:
        use_speechmatics = settings.USE_SPEECHMATICS
    
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
                sm_lang = requested_lang.split("-")[0].lower() if requested_lang else "en"
                start_msg = {
                    "message": "StartRecognition",
                    "audio_format": {
                        "type": "file"
                    },
                    "transcription_config": {
                        "language": sm_lang,
                        "operating_point": "enhanced",
                        "enable_partials": True
                    }
                }
                await sm_ws.send(json.dumps(start_msg))
                await client_ws.send_json({"type": "status", "message": "Listening"})
                
                async def browser_to_speechmatics():
                    try:
                        while True:
                            msg = await client_ws.receive()
                            if "bytes" in msg and msg["bytes"]:
                                await sm_ws.send(msg["bytes"])
                            elif "text" in msg and msg["text"]:
                                pass
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
                            if not transcript and data.get("results"):
                                words = []
                                for res_item in data.get("results", []):
                                    alts = res_item.get("alternatives", [])
                                    if alts and alts[0].get("content"):
                                        words.append(alts[0]["content"])
                                transcript = " ".join(words).strip()

                            if not transcript:
                                continue
                            transcript = correct_transcript(transcript, software)

                        # Predictive Question Engine: Live speculation on interim speech tokens
                        if not bool(data.get("is_final")) and len(transcript.split()) >= 3:
                            try:
                                from app.services.predictive_engine import predictive_engine
                                pred = predictive_engine.predict_from_interim(transcript)
                                if pred.get("predicted") and pred.get("confidence", 0) >= 0.50:
                                    await client_ws.send_json({
                                        "type": "predictive_preview",
                                        "predicted_question": pred["question"],
                                        "confidence": pred["confidence"],
                                        "category": pred["category"],
                                        "intent": pred["intent"]
                                    })
                            except Exception as pred_err:
                                logger.debug(f"[PredictivePreview] Skipped interim speculation: {pred_err}")

                            is_final = (msg_type == "AddTranscript")
                            # In Speechmatics, AddTranscript is always a complete finalized segment
                            speech_final = is_final
                            logger.info(f"[Speechmatics] Session {session_id} - Transcript: '{transcript}', is_final={is_final}")

                            if is_final:
                                current = await redis_cache.get_transcript(session_id) or ""
                                updated = f"{current} {transcript}".strip()
                                await redis_cache.set_transcript(session_id, updated)

                                # Compute real pause duration from inter-transcript gap
                                now_ts = time.time()
                                last_ts = _last_transcript_time.get(session_id, now_ts)
                                actual_pause = now_ts - last_ts
                                _last_transcript_time[session_id] = now_ts

                                asyncio.create_task(run_background_pipeline(session_id, updated, client_ws, actual_pause))

                            await client_ws.send_json({
                                "type": "transcript",
                                "text": transcript,
                                "is_final": is_final,
                                "speech_final": speech_final,
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
                        msg = await client_ws.receive()
                        if "bytes" in msg and msg["bytes"]:
                            await dg_ws.send(msg["bytes"])
                        elif "text" in msg and msg["text"]:
                            try:
                                data = json.loads(msg["text"])
                                if isinstance(data, dict):
                                    evt_name = data.get("event") or data.get("type") or data.get("action", "")

                                    # 1. Answer Request over WebSocket (Unified Gateway)
                                    if evt_name in ("answer.request", "request_answer"):
                                        question_text = data.get("question") or ""
                                        model_req = data.get("model")
                                        pred_id = data.get("prediction_id") or str(uuid.uuid4())
                                        ans_id = data.get("answer_id") or str(uuid.uuid4())
                                        curr_ver = await redis_cache.increment_generation_version(session_id)
                                        _generation_versions[session_id] = curr_ver

                                        # Cancel any previously running answer generation for this session
                                        if session_id in _active_generation_tasks:
                                            old_gen = _active_generation_tasks.pop(session_id, None)
                                            if old_gen and not old_gen.done():
                                                old_gen.cancel()

                                        gen_task = asyncio.create_task(
                                            stream_answer_over_websocket(
                                                session_id=session_id,
                                                question=question_text,
                                                model=model_req,
                                                client_ws=client_ws,
                                                prediction_id=pred_id,
                                                version=curr_ver,
                                                source_type=data.get("source_type", "transcript"),
                                                answer_id=ans_id
                                            )
                                        )
                                        _active_generation_tasks[session_id] = gen_task
                                        await client_ws.send_json({
                                            "protocol_version": "2.0",
                                            "event_id": str(uuid.uuid4()),
                                            "event": "ack",
                                            "reply_to": "answer.request",
                                            "status": "started",
                                            "answer_id": ans_id,
                                            "prediction_id": pred_id,
                                            "generation_version": curr_ver
                                        })

                                    # 2. Prediction / Answer Cancellation (Stale Token Guard)
                                    elif evt_name in ("prediction.cancel", "answer.cancel", "cancel", "abort", "cancel_stream"):
                                        curr_ver = await redis_cache.increment_generation_version(session_id)
                                        _generation_versions[session_id] = curr_ver
                                        if session_id in _active_generation_tasks:
                                            old_gen = _active_generation_tasks.pop(session_id, None)
                                            if old_gen and not old_gen.done():
                                                old_gen.cancel()
                                        if session_id in _active_analysis_tasks:
                                            old_task = _active_analysis_tasks.pop(session_id, None)
                                            if old_task and not old_task.done():
                                                old_task.cancel()
                                        await client_ws.send_json({
                                            "protocol_version": "2.0",
                                            "event_id": str(uuid.uuid4()),
                                            "event": "prediction.cancelled",
                                            "session_id": session_id,
                                            "prediction_id": data.get("prediction_id"),
                                            "generation_version": curr_ver
                                        })

                                    # 3. Heartbeat / KeepAlive
                                    elif evt_name in ("heartbeat", "ping"):
                                        await client_ws.send_json({"event": "heartbeat", "timestamp": time.time()})
                                    elif evt_name == "KeepAlive":
                                        await dg_ws.send(json.dumps({"type": "KeepAlive"}))

                                    # 4. Session Start Acknowledgement
                                    elif evt_name in ("session.start", "init"):
                                        await client_ws.send_json({
                                            "event": "ack",
                                            "reply_to": "session.start",
                                            "status": "ready",
                                            "session_id": session_id
                                        })
                            except Exception as text_err:
                                logger.debug(f"Error parsing client text frame: {text_err}")
                except WebSocketDisconnect:
                    pass
                except Exception as e:
                    logger.debug(f"Browser connection closed or error: {e}")
                finally:
                    # Browser is gone: end the Deepgram stream so deepgram_to_browser (and the gather) finish
                    # instead of the keep-alive holding an orphaned, billed connection open.
                    try:
                        await dg_ws.send(json.dumps({"type": "CloseStream"}))
                        await dg_ws.close()
                    except Exception:
                        pass

            async def deepgram_to_browser():
                # Track the accumulated text for the current utterance to avoid Redis duplication
                current_utterance_parts: list = []

                async for raw in dg_ws:
                    try:
                        data = json.loads(raw)
                    except Exception:
                        continue

                    try:
                        # Handle case where payload is a list or contains a list of events
                        if not isinstance(data, dict):
                            if isinstance(data, list) and data and isinstance(data[0], dict):
                                data = data[0]
                            else:
                                continue

                        msg_type = data.get("type")

                        # Handle Deepgram UtteranceEnd event (fired when utterance_end_ms silence detected)
                        # This is the most reliable "speaker stopped talking" signal
                        if msg_type == "UtteranceEnd":
                            logger.debug(f"[Deepgram] Session {session_id} - UtteranceEnd event received")
                            try:
                                await client_ws.send_json({
                                    "type": "utterance_end",
                                })
                            except Exception:
                                pass
                            continue

                        # Ignore non-transcript Deepgram lifecycle and VAD events
                        # (e.g. SpeechStarted has channel: [0, 0] which is a list of channel indices, not a channel dict)
                        if msg_type in ("SpeechStarted", "Metadata", "CloseStream", "Flushed"):
                            logger.debug(f"[Deepgram] Session {session_id} - {msg_type} event received")
                            continue

                        # Safely extract channel dict (handles single-channel dict, multi-channel list, and non-dict cases)
                        channel_data = data.get("channel")
                        if not isinstance(channel_data, dict):
                            channels_list = data.get("channels")
                            if isinstance(channels_list, list) and channels_list and isinstance(channels_list[0], dict):
                                channel_data = channels_list[0]
                            elif isinstance(channel_data, list) and channel_data and isinstance(channel_data[0], dict):
                                channel_data = channel_data[0]
                            else:
                                # Channel is non-dictionary or missing
                                continue

                        alternatives = channel_data.get("alternatives", [])
                        if not isinstance(alternatives, list) or not alternatives or not isinstance(alternatives[0], dict):
                            continue

                        transcript = str(alternatives[0].get("transcript", "")).strip()
                        if not transcript:
                            continue
                        # Role-aware fix-ups ("hoops" -> "OOPS" for software roles) before the candidate sees it.
                        transcript = correct_transcript(transcript, software)

                        # Predictive Question Engine: Live speculation on interim speech tokens
                        if not bool(data.get("is_final")) and len(transcript.split()) >= 3:
                            try:
                                from app.services.predictive_engine import predictive_engine
                                pred = predictive_engine.predict_from_interim(transcript)
                                if pred.get("predicted") and pred.get("confidence", 0) >= 0.50:
                                    await client_ws.send_json({
                                        "type": "predictive_preview",
                                        "predicted_question": pred["question"],
                                        "confidence": pred["confidence"],
                                        "category": pred["category"],
                                        "intent": pred["intent"]
                                    })
                            except Exception as pred_err:
                                logger.debug(f"[PredictivePreview] Skipped interim speculation: {pred_err}")

                        # Properly distinguish the two Deepgram finality signals:
                        # is_final: This segment/endpoint is finalized (can happen multiple times per sentence)
                        # speech_final: The speaker has actually stopped talking (true end of turn)
                        dg_is_final = bool(data.get("is_final"))
                        dg_speech_final = bool(data.get("speech_final"))
                        # Combined: treat as "final" for display purposes only if segment is finalized
                        is_final = dg_is_final
                        # speech_final is the definitive "speaker done" signal
                        speech_final = dg_speech_final

                        logger.info(
                            f"[Deepgram] Session {session_id} - Transcript: '{transcript}', "
                            f"is_final={dg_is_final}, speech_final={dg_speech_final}"
                        )

                        # Accumulate utterance parts for Redis (only finalized segments)
                        if dg_is_final:
                            current_utterance_parts.append(transcript)

                        # Only commit to Redis when the speaker truly stops talking (speech_final)
                        # This prevents the same sentence being appended 3-4 times
                        if dg_speech_final and current_utterance_parts:
                            # Join all finalized segments that formed this utterance
                            complete_utterance = " ".join(current_utterance_parts).strip()
                            current = await redis_cache.get_transcript(session_id) or ""
                            updated = f"{current} {complete_utterance}".strip()
                            await redis_cache.set_transcript(session_id, updated)
                            current_utterance_parts.clear()

                            # Compute real pause duration from inter-transcript gap
                            now_ts = time.time()
                            last_ts = _last_transcript_time.get(session_id, now_ts)
                            actual_pause = now_ts - last_ts
                            _last_transcript_time[session_id] = now_ts

                            # Run pipeline on the CURRENT utterance so prediction and intent reflect this question
                            asyncio.create_task(run_background_pipeline(session_id, complete_utterance, client_ws, actual_pause))
                        elif dg_is_final and not dg_speech_final:
                            # For non-speech_final is_final segments, still update timestamp
                            _last_transcript_time[session_id] = time.time()

                        await client_ws.send_json({
                            "type": "transcript",
                            "text": transcript,
                            "is_final": is_final,
                            "speech_final": speech_final,
                        })
                    except Exception as frame_err:
                        logger.warning(f"[Deepgram] Session {session_id} - error processing message: {frame_err}")
                        continue

            async def deepgram_keepalive():
                # Deepgram closes the stream (NET-0001) after ~10s without data. The browser only sends
                # audio while its MediaRecorder runs and never sends KeepAlive, so cover silent gaps here.
                while True:
                    await asyncio.sleep(5)
                    await dg_ws.send(json.dumps({"type": "KeepAlive"}))

            keepalive_task = asyncio.create_task(deepgram_keepalive())
            try:
                await asyncio.gather(browser_to_deepgram(), deepgram_to_browser())
            finally:
                keepalive_task.cancel()

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
        writer_task.cancel()
        _outbound_queues.pop(session_id, None)
        await redis_cache.track_websocket(session_id, client_id, register=False)

@router.websocket("/ws/transcribe/{session_id}")
async def transcribe_session_ws(client_ws: WebSocket, session_id: str):
    await run_websocket_proxy(client_ws, session_id)

@router.websocket("/ws/transcribe")
async def transcribe_default_ws(client_ws: WebSocket):
    session_id = "default_session"
    await run_websocket_proxy(client_ws, session_id)

# Backward-compatible endpoint (current UI)
@router.websocket("/ws/deepgram")
async def legacy_deepgram_ws(client_ws: WebSocket):
    session_id = "legacy-session-id"
    await run_websocket_proxy(client_ws, session_id)

# Scalable path endpoint (future integrations)
@router.websocket("/ws/audio/{session_id}")
async def session_deepgram_ws(client_ws: WebSocket, session_id: str):
    await run_websocket_proxy(client_ws, session_id)
