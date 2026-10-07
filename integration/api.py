from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .service import IntegrationError, IntegrationService


class GameProfileBody(BaseModel):
    language: str = Field(min_length=2, max_length=16)
    engine: str = Field(default="unknown", max_length=80)
    build_version: str = Field(default="", max_length=80)
    platform: str = Field(default="", max_length=80)
    executable_path: str = Field(default="", max_length=1000)
    install_root: str = Field(default="", max_length=1000)
    audio_format: str = Field(default="ogg", pattern=r"^(ogg|wav)$")
    bitrate_kbps: int = Field(default=64, ge=32, le=192)


class BindingBody(BaseModel):
    plugin_record_id: str = Field(min_length=1, max_length=48)
    config: dict = Field(default_factory=dict)


class PluginManifestBody(BaseModel):
    manifest: dict


class TriggerBody(BaseModel):
    runtime_trigger_type: str = Field(min_length=1, max_length=48)
    runtime_trigger_id: str = Field(min_length=1, max_length=160)


class PackageBody(BaseModel):
    version: str = Field(default="0.1.0", min_length=1, max_length=48)


class TestRunBody(BaseModel):
    package_id: str = Field(min_length=1, max_length=48)


class TestItemBody(BaseModel):
    occurrence_id: str = Field(min_length=1, max_length=48)
    received_trigger_id: str | None = Field(default=None, max_length=160)
    played_take_id: str | None = Field(default=None, max_length=48)
    event_type: str = Field(default="dialogue_start", max_length=32)
    latency_ms: int | None = Field(default=None, ge=0, le=600000)


def create_router(service: IntegrationService) -> APIRouter:
    router = APIRouter(prefix="/api/integration", tags=["integration"])

    def call(operation, *args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except IntegrationError as error:
            raise HTTPException(status_code=error.status_code,
                                detail={"code": error.code, "message": error.message}) from error

    @router.get("/profile")
    def get_profile():
        return service.profile()

    @router.put("/profile")
    def put_profile(body: GameProfileBody):
        return call(service.save_profile, body.model_dump())

    @router.get("/plugins")
    def list_plugins():
        return service.plugins()

    @router.post("/plugins/register", status_code=201)
    def register_plugin(body: PluginManifestBody):
        return call(service.register_plugin, body.manifest)

    @router.post("/bindings")
    def bind_plugin(body: BindingBody):
        return call(service.bind_plugin, body.plugin_record_id, body.config)

    @router.get("/mapping")
    def get_mapping(limit: int = Query(default=250, ge=1, le=500),
                    offset: int = Query(default=0, ge=0)):
        return service.mapping(limit=limit, offset=offset)

    @router.put("/mapping/{occurrence_id}")
    def put_mapping(occurrence_id: str, body: TriggerBody):
        return call(service.map_trigger, occurrence_id, body.runtime_trigger_type,
                    body.runtime_trigger_id)

    @router.post("/packages")
    def create_package(body: PackageBody):
        return call(service.build_package, body.version)

    @router.get("/packages/{package_id}/download")
    def download_package(package_id: str):
        path = call(service.package_file, package_id)
        return FileResponse(path, media_type="application/zip", filename=Path(path).name)

    @router.get("/test-runs")
    def list_test_runs():
        return service.test_runs()

    @router.post("/test-runs")
    def create_test_run(body: TestRunBody):
        return call(service.start_test_run, body.package_id)

    @router.post("/test-runs/{test_run_id}/items")
    def add_test_item(test_run_id: str, body: TestItemBody):
        return call(service.record_test_item, test_run_id, body.model_dump())

    @router.post("/test-runs/{test_run_id}/finish")
    def finish_test_run(test_run_id: str):
        return call(service.finish_test_run, test_run_id)

    return router
