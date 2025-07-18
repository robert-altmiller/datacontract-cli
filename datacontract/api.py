import logging
import os
from typing import Annotated, Optional

import typer
from fastapi import Body, Depends, FastAPI, HTTPException, Query, status
from fastapi.responses import PlainTextResponse
from fastapi.security.api_key import APIKeyHeader

from datacontract.data_contract import DataContract, ExportFormat
from datacontract.model.run import Run

DATA_CONTRACT_EXAMPLE_PAYLOAD = """dataContractSpecification: 1.2.0
id: urn:datacontract:checkout:orders-latest
info:
  title: Orders Latest
  version: 2.0.0
  owner: Sales Team
servers:
  production:
    type: s3
    location: s3://datacontract-example-orders-latest/v2/{model}/*.json
    format: json
    delimiter: new_line
models:
  orders:
    description: One record per order. Includes cancelled and deleted orders.
    type: table
    fields:
      order_id:
        type: string
        primaryKey: true
      order_timestamp:
        description: The business timestamp in UTC when the order was successfully registered in the source system and the payment was successful.
        type: timestamp
        required: true
        examples:
          - "2024-09-09T08:30:00Z"
      order_total:
        description: Total amount the smallest monetary unit (e.g., cents).
        type: long
        required: true
        examples:
          - 9999
        quality:
          - type: sql
            description: 95% of all order total values are expected to be between 10 and 499 EUR.
            query: |
              SELECT quantile_cont(order_total, 0.95) AS percentile_95
              FROM orders
            mustBeBetween: [1000, 99900]
      customer_id:
        description: Unique identifier for the customer.
        type: text
        minLength: 10
        maxLength: 20
"""

app = FastAPI(
    docs_url="/",
    title="Data Contract CLI API",
    summary="You can use the API to test, export, and lint your data contracts.",
    license_info={
        "name": "MIT License",
        "identifier": "MIT",
    },
    contact={"name": "Data Contract CLI", "url": "https://cli.datacontract.com/"},
    openapi_tags=[
        {
            "name": "test",
            "externalDocs": {
                "description": "Documentation",
                "url": "https://cli.datacontract.com/#test",
            },
        },
        {
            "name": "lint",
            "externalDocs": {
                "description": "Documentation",
                "url": "https://cli.datacontract.com/#lint",
            },
        },
        {
            "name": "export",
            "externalDocs": {
                "description": "Documentation",
                "url": "https://cli.datacontract.com/#export",
            },
        },
    ],
)

api_key_header = APIKeyHeader(
    name="x-api-key",
    auto_error=False,  # this makes authentication optional
)


def check_api_key(api_key_header: str | None):
    correct_api_key = os.getenv("DATACONTRACT_CLI_API_KEY")
    if correct_api_key is None or correct_api_key == "":
        logging.info("Environment variable DATACONTRACT_CLI_API_KEY is not set. Skip API key check.")
        return
    if api_key_header is None or api_key_header == "":
        logging.info("The API key is missing.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key. Use Header 'x-api-key' to provide the API key.",
        )
    if api_key_header != correct_api_key:
        logging.info("The provided API key is not correct.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The provided API key is not correct.",
        )
    logging.info("Request authenticated with API key.")
    pass


@app.post(
    "/test",
    tags=["test"],
    summary="Run data contract tests",
    description="""
              Run schema and quality tests. Data Contract CLI connects to the data sources configured in the server section.
              This usually requires credentials to access the data sources.
              Credentials must be provided via environment variables when running the web server.
              POST the data contract YAML as payload.
            """,
    responses={
        401: {
            "description": "Unauthorized (when an environment variable DATACONTRACT_CLI_API_KEY is configured).",
            "content": {
                "application/json": {
                    "examples": {
                        "api_key_missing": {
                            "summary": "API key Missing",
                            "value": {"detail": "Missing API key. Use Header 'x-api-key' to provide the API key."},
                        },
                        "api_key_wrong": {
                            "summary": "API key Wrong",
                            "value": {"detail": "The provided API key is not correct."},
                        },
                    }
                }
            },
        },
    },
    response_model_exclude_none=True,
    response_model_exclude_unset=True,
)
async def test(
    body: Annotated[
        str,
        Body(
            title="Data Contract YAML",
            media_type="application/yaml",
            examples=[DATA_CONTRACT_EXAMPLE_PAYLOAD],
        ),
    ],
    api_key: Annotated[str | None, Depends(api_key_header)] = None,
    server: Annotated[
        str | None,
        Query(
            examples=["production"],
            description="The server name to test. Optional, if there is only one server.",
        ),
    ] = None,
) -> Run:
    check_api_key(api_key)
    logging.info("Testing data contract...")
    logging.info(body)
    return DataContract(data_contract_str=body, server=server).test()


@app.post(
    "/lint",
    tags=["lint"],
    summary="Validate that the datacontract.yaml is correctly formatted.",
    description="""Validate that the datacontract.yaml is correctly formatted.""",
)
async def lint(
    body: Annotated[
        str,
        Body(
            title="Data Contract YAML",
            media_type="application/yaml",
            examples=[DATA_CONTRACT_EXAMPLE_PAYLOAD],
        ),
    ],
    schema: Annotated[
        str | None,
        Query(
            examples=["https://datacontract.com/datacontract.schema.json"],
            description="The schema to use for validation. This must be a URL.",
        ),
    ] = None,
):
    data_contract = DataContract(data_contract_str=body, schema_location=schema)
    lint_result = data_contract.lint()
    return {"result": lint_result.result, "checks": lint_result.checks}


@app.post(
    "/export",
    tags=["export"],
    summary="Convert data contract to a specific format.",
    response_class=PlainTextResponse,
)
def export(
    body: Annotated[
        str,
        Body(
            title="Data Contract YAML",
            media_type="application/yaml",
            examples=[DATA_CONTRACT_EXAMPLE_PAYLOAD],
        ),
    ],
    format: Annotated[ExportFormat, typer.Option(help="The export format.")],
    server: Annotated[
        str | None,
        Query(
            examples=["production"],
            description="The server name to export. Optional, if there is only one server.",
        ),
    ] = None,
    model: Annotated[
        str | None,
        Query(
            description="Use the key of the model in the data contract yaml file "
            "to refer to a model, e.g., `orders`, or `all` for all "
            "models (default).",
        ),
    ] = "all",
    rdf_base: Annotated[
        Optional[str],
        typer.Option(help="[rdf] The base URI used to generate the RDF graph.", rich_help_panel="RDF Options"),
    ] = None,
    sql_server_type: Annotated[
        Optional[str],
        Query(
            description="[sql] The server type to determine the sql dialect. By default, it uses 'auto' to automatically detect the sql dialect via the specified servers in the data contract.",
        ),
    ] = None,
):
    result = DataContract(data_contract_str=body, server=server).export(
        export_format=format,
        model=model,
        rdf_base=rdf_base,
        sql_server_type=sql_server_type,
    )

    return result
