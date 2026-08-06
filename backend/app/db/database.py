import logging
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from app.core.config import settings

logger = logging.getLogger("copilotx.database")

class Base(DeclarativeBase):
    pass

engine = None
SessionLocal = None

def setup_database_session(database_url: str):
    global engine, SessionLocal
    is_sqlite = database_url.startswith("sqlite")
    
    # SQLite doesn't support pool_pre_ping or pool size settings in the same way
    connect_args = {}
    if is_sqlite:
        connect_args["check_same_thread"] = False
        
    engine = create_async_engine(
        database_url,
        pool_pre_ping=not is_sqlite,
        connect_args=connect_args,
        future=True
    )
    SessionLocal = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False
    )

# Setup initial primary engine
setup_database_session(settings.DATABASE_URL)

async def verify_and_initialize_db():
    global engine, SessionLocal
    
    async def run_schema_updates(conn):
        import sqlalchemy as sa
        is_sqlite = str(conn.engine.url).startswith("sqlite")
        new_cols = ["introduction", "professional_summary", "career_journey", "strengths", "project_summary"]
        for col in new_cols:
            try:
                async with conn.begin_nested():
                    if is_sqlite:
                        await conn.execute(sa.text(f"ALTER TABLE resumes ADD COLUMN {col} TEXT"))
                    else:
                        await conn.execute(sa.text(f"ALTER TABLE resumes ADD COLUMN IF NOT EXISTS {col} TEXT"))
            except Exception:
                pass
        
        try:
            async with conn.begin_nested():
                if is_sqlite:
                    await conn.execute(sa.text("ALTER TABLE sessions ADD COLUMN summary TEXT"))
                else:
                    await conn.execute(sa.text("ALTER TABLE sessions ADD COLUMN IF NOT EXISTS summary TEXT"))
        except Exception:
            pass

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await run_schema_updates(conn)
        logger.info(f"Database tables verified/created on connection: {engine.url}")
    except Exception as e:
        if not settings.DATABASE_URL.startswith("sqlite"):
            logger.warning(f"Primary database connection failed: {e}. Falling back to SQLite...")
            fallback_url = "sqlite+aiosqlite:///./copilotx.db"
            setup_database_session(fallback_url)
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
                await run_schema_updates(conn)
            logger.info("SQLite fallback database initialized successfully.")
        else:
            logger.error(f"Database initialization failed: {e}")
            raise

async def get_db():
    global SessionLocal
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

