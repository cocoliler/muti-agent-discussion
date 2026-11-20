"""Local API and persistence for the scripted discussion prototype."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
from uuid import UUID

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .scenario import APPROVED_STAGE, BACKEND, scenario_draft, scenario_events

DB = Path(os.getenv("DISCUSSION_DB", str(BACKEND / ".data/discussions.sqlite3")))
app = FastAPI(title="多 Agent 需求讨论原型", version="0.1.0")
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])


class Advance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_stage: int = Field(ge=0, le=4, strict=True)


class Confirm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1, strict=True)


@contextmanager
def database(write=False):
    DB.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB, timeout=5)
    try:
        with connection:
            connection.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, snapshot TEXT NOT NULL)")
            if write:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
    finally:
        connection.close()


def load(connection, session_id):
    row = connection.execute("SELECT snapshot FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "找不到此讨论会话")
    return json.loads(row[0])


def store(connection, state):
    connection.execute(
        "INSERT INTO sessions VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET snapshot = excluded.snapshot",
        (state["id"], json.dumps(state, ensure_ascii=False)),
    )


def append_phase(state, stage):
    state["stage"] = stage
    state["messages"].extend(scenario_events(stage, len(state["messages"])))
    if stage in (3, 4):
        state["drafts"].append(scenario_draft(stage - 2))
    return state


@app.put("/api/demo/{session_id}")
def start(session_id: UUID):
    key = str(session_id)
    with database(write=True) as connection:
        row = connection.execute("SELECT snapshot FROM sessions WHERE id = ?", (key,)).fetchone()
        if row is not None:
            return json.loads(row[0])
        state = append_phase({"id": key, "messages": [], "drafts": []}, 0)
        store(connection, state)
        return state


@app.get("/api/demo/{session_id}")
def read(session_id: UUID):
    with database() as connection:
        return load(connection, str(session_id))


@app.post("/api/demo/{session_id}/advance")
def advance(session_id: UUID, payload: Advance):
    with database(write=True) as connection:
        state = load(connection, str(session_id))
        if state["stage"] > payload.expected_stage:
            return state
        if state["stage"] != payload.expected_stage or state["stage"] >= 4:
            raise HTTPException(409, "当前阶段不可推进，请刷新会话")
        append_phase(state, state["stage"] + 1)
        store(connection, state)
        return state


@app.post("/api/demo/{session_id}/confirm")
def confirm(session_id: UUID, payload: Confirm):
    with database(write=True) as connection:
        state = load(connection, str(session_id))
        draft = state["drafts"][-1] if state["drafts"] else None
        if state["stage"] < 4 or draft is None or draft["revision"] != payload.revision or draft["verdict"] != "PASS":
            raise HTTPException(409, "仅可签收已通过评审的当前规格")
        if state["stage"] == 4:
            append_phase(state, APPROVED_STAGE)
            store(connection, state)
        return state


@app.get("/api/demo/{session_id}/export")
def export(session_id: UUID):
    with database() as connection:
        state = load(connection, str(session_id))
        if state["stage"] != APPROVED_STAGE:
            raise HTTPException(409, "请先签收当前规格")
        return JSONResponse(state["drafts"][-1]["content"], headers={
            "Content-Disposition": 'attachment; filename="meeting-room.design-spec.json"'
        })


dist = BACKEND.parent / "frontend/dist"
if (dist / "index.html").is_file():
    app.mount("/", StaticFiles(directory=dist, html=True), name="workspace")
