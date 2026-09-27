from __future__ import annotations
import asyncio
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

class Search(BaseModel):
    query:str=Field(min_length=1,max_length=1000)
    session_id:str
    start_elapsed_ms:int=0
    end_elapsed_ms:int|None=None
    limit:int=Field(default=6,ge=1,le=8)

class TrackRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    evidence_id:str
    points:list[list[float]]
    labels:list[int]
    label:str
    preview_only:bool=False


def router(memory):
    api=APIRouter(prefix='/api/memory')
    @api.get('/status')
    async def status(): return memory.status()
    @api.get('/frames')
    async def frames(session_id:str,start:int=0,limit:int=200):
        try: return {'frames':memory.store.frames(session_id,start,limit=limit)}
        except ValueError as exc: raise HTTPException(422,str(exc)) from exc
    @api.get('/state')
    async def state(session_id:str,at:int|None=None): return memory.store.state(session_id,at)
    @api.get('/changes')
    async def changes(session_id:str,start:int=0,end:int|None=None): return memory.store.changes(session_id,start,end)
    @api.post('/search')
    async def search(request:Search):
        try: return await memory.search(request.query,request.session_id,request.start_elapsed_ms,request.end_elapsed_ms,request.limit)
        except Exception as exc: raise HTTPException(409,str(exc)) from exc
    @api.post('/tracks')
    async def start(request:TrackRequest):
        try: return memory.start_track(**request.model_dump())
        except ValueError as exc: raise HTTPException(422,str(exc)) from exc
    @api.post('/tracks/{tid}/stop')
    async def stop(tid:str):
        try: return memory.stop_track(tid)
        except ValueError as exc: raise HTTPException(404,str(exc)) from exc
    @api.get('/tracks/{tid}')
    async def track(tid:str,after:int=0,limit:int=8):
        if after<0 or not 1<=limit<=100: raise HTTPException(422,'Invalid page')
        try: return {'track':memory.store.track(tid),'frames':memory.store.track_frames(tid,limit,after)}
        except ValueError as exc: raise HTTPException(404,str(exc)) from exc
    @api.get('/asset/{asset:path}')
    async def asset(asset:str):
        try:
            path=memory.store.asset(asset)
            if asset.split('/')[0] not in ('evidence','tracks') or path.suffix not in ('.jpg','.png') or not path.is_file():
                raise ValueError('Unknown image asset')
            return FileResponse(path)
        except ValueError as exc: raise HTTPException(404,str(exc)) from exc
    return api
