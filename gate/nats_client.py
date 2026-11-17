import os
import json
import logging
import nats
from nats.js.api import StreamConfig

logger = logging.getLogger("gate.nats")
NATS_URL = os.getenv("NATS_URL", "nats://localhost:4222")
STREAM_NAME = "SAFI_ORCH"
SUBJECT_PREFIX = "safi.orchestration"

nc = None
js = None

async def init_nats():
    global nc, js
    try:
        nc = await nats.connect(NATS_URL)
        js = nc.jetstream()
        
        # Ensure JetStream stream exists with fixed subject routing (multi-level wildcard >)
        try:
            await js.add_stream(
                StreamConfig(
                    name=STREAM_NAME,
                    subjects=[f"{SUBJECT_PREFIX}.>"]
                )
            )
            logger.info("JetStream stream '%s' initialized with %s.>", STREAM_NAME, SUBJECT_PREFIX)
        except Exception:
            # Stream may already exist, update its subjects
            try:
                await js.update_stream(
                    StreamConfig(
                        name=STREAM_NAME,
                        subjects=[f"{SUBJECT_PREFIX}.>"]
                    )
                )
                logger.info("JetStream stream '%s' updated to %s.>", STREAM_NAME, SUBJECT_PREFIX)
            except Exception as e:
                logger.info("JetStream stream note: %s", e)
    except Exception as e:
        logger.error("Failed to connect to NATS at %s: %s", NATS_URL, e)

async def publish_event(event_type: str, data: dict):
    global js
    if js is None:
        logger.warning("JetStream not available, skipping event: %s", event_type)
        return
    
    subject = f"{SUBJECT_PREFIX}.{event_type}"
    payload = json.dumps(data).encode("utf-8")
    try:
        ack = await js.publish(subject, payload)
        logger.info("Event published to %s (seq: %s)", subject, ack.seq)
    except Exception as e:
        logger.error("Failed to publish event to JetStream: %s", e)

async def close_nats():
    global nc
    if nc and not nc.is_closed:
        await nc.drain()
