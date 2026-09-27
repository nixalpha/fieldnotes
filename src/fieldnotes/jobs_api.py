"""Local HTTP adapter for the shared Jobs service."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .jobs import JobError


class Mutation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str = Field(min_length=1, max_length=200)


class CreateJob(Mutation):
    name: str
    theme: str


class UpdateJob(Mutation):
    expected_revision: int = Field(ge=1)
    name: str | None = None
    theme: str | None = None


class SwitchJob(Mutation):
    job_id: str | None
    expected_selection_revision: int = Field(ge=0)


class AssignObservations(Mutation):
    session_ids: list[str] = Field(min_length=1, max_length=100)


def router(jobs):
    api = APIRouter(prefix='/api/jobs')

    def invoke(action, *args, **kwargs):
        try:
            return action(*args, **kwargs)
        except JobError as exc:
            raise HTTPException(exc.status, detail=str(exc)) from exc

    @api.get('')
    async def list_jobs(limit: int = 50, cursor: str | None = None):
        return invoke(jobs.list, limit, cursor)

    @api.post('')
    async def create_job(body: CreateJob):
        return invoke(jobs.create, **body.model_dump())

    @api.get('/context')
    async def context():
        return invoke(jobs.context)

    @api.post('/selection')
    async def switch_job(body: SwitchJob):
        return invoke(jobs.switch, **body.model_dump())

    @api.get('/{job_id}')
    async def get_job(job_id: str):
        return invoke(jobs.get, job_id)

    @api.post('/{job_id}')
    async def update_job(job_id: str, body: UpdateJob):
        return invoke(jobs.update, job_id=job_id, **body.model_dump())

    @api.post('/{job_id}/observations')
    async def assign(job_id: str, body: AssignObservations):
        return invoke(jobs.assign, job_id=job_id, **body.model_dump())

    return api
