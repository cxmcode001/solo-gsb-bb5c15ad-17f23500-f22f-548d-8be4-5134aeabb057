"""FastAPI application exposing the compliance rule engine.

Endpoints:
    GET  /health    - liveness/readiness probe
    POST /evaluate  - evaluate a named target rule against provided facts

The service is fully local: no external accounts, databases or paid
services are required.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import __version__
from .engine import RuleError, evaluate

logger = logging.getLogger("rule_evaluator")


class EvaluateRequest(BaseModel):
    facts: dict[str, Any] = Field(
        default_factory=dict,
        description='Fact dictionary, e.g. {"age": 17, "region": {"code": "CN"}}',
    )
    rules: dict[str, dict[str, Any]] = Field(
        ..., description="Named rule nodes. Keys are rule names used by ref/target."
    )
    target: str = Field(..., description="Name of the rule to evaluate.")


class EvaluateResponse(BaseModel):
    result: str = Field(..., description='"true", "false" or "unknown"')
    used_facts: list[str] = Field(..., description="Fact fields actually read (sorted).")
    missing_fields: list[str] = Field(
        ..., description="Referenced fact fields not present in the request (sorted)."
    )
    hit_paths: list[str] = Field(
        ..., description="All rule node paths visited during evaluation (sorted)."
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> Any:
    logger.info("compliance rule evaluator %s started", __version__)
    yield
    logger.info("compliance rule evaluator stopped")


app = FastAPI(
    title="Compliance Rule Evaluation API",
    version=__version__,
    description=(
        "Evaluates named compliance rules over a fact dictionary using "
        "all/any/not/ref nodes, eq/ne/gt/gte/lt/lte comparisons and "
        "true/false/unknown three-valued logic."
    ),
    lifespan=lifespan,
)


@app.exception_handler(RuleError)
async def rule_error_handler(request: Request, exc: RuleError) -> JSONResponse:
    return JSONResponse(status_code=400, content=exc.to_dict())


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.post(
    "/evaluate",
    response_model=EvaluateResponse,
    tags=["rules"],
    responses={
        400: {
            "description": "Malformed rules, missing/unknown references or a reference cycle",
            "content": {
                "application/json": {
                    "example": {
                        "error": "missing_reference",
                        "message": "1 referenced rule(s) are not defined",
                        "details": [{"missing_rule": "is_minor", "at": "adult.not.ref"}],
                    }
                }
            },
        }
    },
)
async def post_evaluate(payload: EvaluateRequest) -> dict[str, Any]:
    # validate_rules(...) runs first inside evaluate(), rejecting missing
    # references and cycles before any ternary evaluation takes place.
    return evaluate(rules=payload.rules, facts=payload.facts, target=payload.target)
