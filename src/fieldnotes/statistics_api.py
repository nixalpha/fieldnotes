"""Local HTTP adapter for job statistics and charts."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .jobs import JobError
from .statistics import ChartSpec


class Mutation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str = Field(min_length=1, max_length=200)


class ChartRequest(Mutation):
    prompt: str = Field(min_length=1, max_length=2000)


class RecordChart(Mutation):
    spec: ChartSpec
    prompt: str | None = Field(default=None, max_length=2000)


def router(statistics):
    api = APIRouter(prefix='/api/jobs')

    def invoke(action, *args, **kwargs):
        try:
            return action(*args, **kwargs)
        except JobError as exc:
            raise HTTPException(exc.status, detail=str(exc)) from exc

    @api.get('/{job_id}/statistics')
    async def summary(job_id: str):
        return invoke(statistics.summary, job_id)

    @api.get('/{job_id}/statistics/metrics/{metric}')
    async def metric(job_id: str, metric: str, bucket_seconds: int | None = None):
        return invoke(statistics.metric, job_id, metric, bucket_seconds).model_dump()

    @api.get('/{job_id}/statistics/observations')
    async def observations(job_id: str, cursor: str | None = None, limit: int = 50,
                           start_at: str | None = None, end_at: str | None = None):
        return invoke(statistics.observations, job_id, cursor, limit, start_at, end_at)

    @api.get('/{job_id}/charts')
    async def charts(job_id: str):
        return invoke(statistics.charts, job_id)

    @api.post('/{job_id}/charts')
    async def record(job_id: str, body: RecordChart):
        return invoke(statistics.record, body.request_id, job_id, body.spec, prompt=body.prompt, source='manual')

    @api.get('/{job_id}/charts/requests')
    async def requests(job_id: str):
        return invoke(statistics.requests, job_id)

    @api.post('/{job_id}/charts/requests')
    async def request(job_id: str, body: ChartRequest):
        return invoke(statistics.request, body.request_id, job_id, body.prompt)

    @api.post('/{job_id}/charts/{chart_id}/delete')
    async def delete(job_id: str, chart_id: str, body: Mutation):
        return invoke(statistics.delete, body.request_id, job_id, chart_id)

    return api
