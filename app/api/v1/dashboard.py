import asyncio
import json
import logging
from typing import Optional

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from jose import JWTError, jwt
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import ALGORITHM, require_role
from app.core.config import settings
from app.core.database import get_db
from app.models.kpi import KpiSnapshot

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/kpis")
async def get_current_kpis(
    current_user=Depends(require_role("admin", "advisor")),
):
    """Retorna el último snapshot de KPIs desde Redis (calculado cada 30s por el worker)."""
    r = aioredis.from_url(settings.REDIS_URL)
    try:
        data = await r.get("kpi:latest")
        if data:
            return json.loads(data)
        return {"message": "KPIs aún no calculados, espera 30s"}
    except Exception as e:
        logger.error(f"Error leyendo KPIs de Redis: {e}")
        raise HTTPException(status_code=503, detail="No se pudo conectar a Redis")
    finally:
        await r.aclose()


@router.get("/history")
async def get_kpi_history(
    agent: str = Query("global"),
    metric: str = Query("users_registered"),
    limit: int = Query(50, ge=1, le=200),
    current_user=Depends(require_role("admin", "advisor")),
    db: AsyncSession = Depends(get_db),
):
    """Retorna snapshots históricos de un KPI (agent + metric) ordenados cronológicamente."""
    result = await db.execute(
        select(KpiSnapshot)
        .where(and_(KpiSnapshot.agent == agent, KpiSnapshot.metric_name == metric))
        .order_by(KpiSnapshot.recorded_at.desc())
        .limit(limit)
    )
    snapshots = result.scalars().all()
    return [
        {"value": s.value, "recorded_at": s.recorded_at.isoformat()}
        for s in reversed(snapshots)
    ]


def _decode_ws_token(token: str) -> Optional[dict]:
    """Decodifica JWT para autenticación WebSocket via query param."""
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None


@router.websocket("/ws")
async def dashboard_websocket(websocket: WebSocket):
    """
    Stream de KPIs en tiempo real via Redis Pub/Sub.

    Autenticación: el cliente pasa JWT como query param `?token=<jwt>`.
    Los browsers no permiten setear headers en WebSocket, por eso se usa query.

    Al conectar:
      1. Valida JWT y rol (admin/advisor) — rechaza con 1008 si inválido
      2. Envía último snapshot cacheado en `kpi:latest` (si existe)
      3. Se suscribe a canal `kpi:updates` y reenvía cada mensaje

    Ping implícito cada 5s via `receive_text()` con timeout 0.1s — detecta
    desconexión del cliente sin bloquear el loop de pubsub.
    """
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Token requerido")
        return

    payload = _decode_ws_token(token)
    if not payload:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Token inválido")
        return

    role = payload.get("role", "buyer")
    if role not in ("admin", "advisor"):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Permisos insuficientes")
        return

    await websocket.accept()

    r = aioredis.from_url(settings.REDIS_URL)
    pubsub = None

    try:
        # Enviar último snapshot inmediatamente
        latest = await r.get("kpi:latest")
        if latest:
            text = latest.decode() if isinstance(latest, (bytes, bytearray)) else latest
            await websocket.send_text(text)

        # Suscribirse a updates
        pubsub = r.pubsub()
        await pubsub.subscribe("kpi:updates")

        while True:
            try:
                message = await asyncio.wait_for(
                    pubsub.get_message(ignore_subscribe_messages=True),
                    timeout=5.0,
                )
                if message and message.get("type") == "message":
                    data = message["data"]
                    if isinstance(data, (bytes, bytearray)):
                        data = data.decode()
                    await websocket.send_text(data)

                # Ping implícito para detectar desconexión del cliente
                try:
                    await asyncio.wait_for(websocket.receive_text(), timeout=0.1)
                except asyncio.TimeoutError:
                    pass  # Cliente sigue conectado, sin mensaje
            except asyncio.TimeoutError:
                # Timeout del get_message — continuar loop
                continue
            except WebSocketDisconnect:
                logger.info("Dashboard WS: cliente desconectado limpiamente")
                break
    except Exception as e:
        logger.error(f"Dashboard WS error: {e}")
    finally:
        if pubsub is not None:
            try:
                await pubsub.unsubscribe("kpi:updates")
            except Exception:
                pass
            try:
                await pubsub.aclose()
            except Exception:
                pass
        try:
            await r.aclose()
        except Exception:
            pass
