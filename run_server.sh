#!/bin/bash
poetry run uvicorn src.server.server:app --reload